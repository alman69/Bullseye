// Native checks for Scooper.h with a fake servo output. No hardware needed.
// Run: firmware/esp32-scooper/tests/run-native.sh
#include <assert.h>
#include <stdio.h>
#include "Scooper.h"

using namespace scooper;

struct FakeOut {
  bool attached[kJointCount] = {};
  float last[kJointCount] = {};
  void attach(uint8_t i) { attached[i] = true; }
  void detach(uint8_t i) { attached[i] = false; }
  void write(uint8_t i, float deg) {
    assert(attached[i]);  // Never pulse a detached servo.
    last[i] = deg;
  }
};

static uint32_t clock_ms = 0;

template <class S>
static void runFor(S& s, uint32_t ms) {
  for (uint32_t t = 0; t < ms; t += 10) {
    clock_ms += 10;
    s.update(clock_ms);
  }
}

static float expectedServo(uint8_t i, int logical) {
  return (kJoints[i].inverted ? 180 - logical : logical) + kJoints[i].trimDeg;
}

int main() {
  FakeOut out;
  Scooper<FakeOut> s(out);

  // Nothing moves before begin(); the shipped config validates.
  assert(s.state() == State::Relaxed);
  assert(!s.home());
  assert(s.validate());

  // Initialization: both sticks start straight down, pulses on.
  assert(s.begin(clock_ms));
  assert(s.state() == State::Holding);
  for (uint8_t i = 0; i < kJointCount; ++i) {
    assert(out.attached[i]);
    assert(s.angle(i) == kDownDeg);
    assert(out.last[i] == expectedServo(i, kDownDeg));
  }

  // Bad commands are rejected without changing state.
  assert(!s.setJoint(kJointCount, 90, 90));
  assert(!s.setJoint(0, kJoints[0].maxDeg + 1, 90));
  assert(!s.setJoint(0, kJoints[0].minDeg - 1, 90));
  assert(s.state() == State::Holding);

  // Time-based slew: 90 -> 170 at 80 deg/s takes one second.
  assert(s.setJoint(0, 170, 80));
  assert(s.state() == State::Moving);
  runFor(s, 500);
  assert(s.angle(0) >= 125 && s.angle(0) <= 135);
  runFor(s, 600);
  assert(s.angle(0) == 170 && s.state() == State::Holding);

  // A stalled loop is clamped: one huge tick moves at most kMaxTickMs worth.
  assert(s.setJoint(0, 90, 80));
  clock_ms += 5000;
  s.update(clock_ms);
  assert(s.angle(0) >= 150);
  runFor(s, 1200);
  assert(s.angle(0) == 90);

  // Squeeze: both sticks move together through directly to 60 degrees.
  assert(s.squeeze());
  assert(s.state() == State::Sequence);
  assert(!s.home());  // Busy until stopped or finished.
  bool sawOut = false;
  for (int t = 0; t < 2000 && s.state() == State::Sequence; ++t) {
    runFor(s, 10);
    assert(s.angle(0) == s.angle(1));  // Simultaneous: always level with each other.
    if (s.angle(0) == kOutDeg) sawOut = true;
    assert(s.angle(0) >= kOutDeg && s.angle(0) <= kDownDeg);
  }
  assert(sawOut);
  assert(s.state() == State::Holding);
  for (uint8_t i = 0; i < kJointCount; ++i) {
    assert(s.angle(i) == kSqueezeDeg);
    assert(out.last[i] == expectedServo(i, kSqueezeDeg));  // Mirrored for the inverted stick.
  }

  // No timer automatically releases a completed squeeze; PWM keeps holding.
  runFor(s, 60000);
  assert(s.state() == State::Holding);
  for (uint8_t i = 0; i < kJointCount; ++i) {
    assert(s.angle(i) == kSqueezeDeg && out.attached[i]);
  }

  // Explicit Let go moves to 140 degrees and keeps holding there.
  assert(s.release());
  runFor(s, 5000);
  assert(s.state() == State::Holding);
  assert(s.angle(0) == kReleaseDeg && s.angle(1) == kReleaseDeg);
  runFor(s, 60000);
  assert(s.angle(0) == kReleaseDeg && s.angle(1) == kReleaseDeg);
  assert(s.squeeze());
  runFor(s, 5000);

  // Stop mid-release freezes where the sticks are.
  assert(s.release());
  runFor(s, 100);
  s.stop();
  assert(s.state() == State::Holding);
  const int frozen = s.angle(1);
  assert(frozen < kReleaseDeg && frozen > kSqueezeDeg);
  runFor(s, 2000);
  assert(s.angle(1) == frozen);

  // Relax detaches; the next command re-attaches and moves on.
  s.relax();
  assert(s.state() == State::Relaxed);
  for (uint8_t i = 0; i < kJointCount; ++i) assert(!out.attached[i]);
  runFor(s, 500);  // No writes while relaxed (FakeOut asserts on that).
  assert(s.home());
  for (uint8_t i = 0; i < kJointCount; ++i) assert(out.attached[i]);
  runFor(s, 3000);
  for (uint8_t i = 0; i < kJointCount; ++i) assert(s.angle(i) == kDownDeg);

  printf("scooper checks passed\n");
  return 0;
}
