#pragma once
// Scooper motion logic. No Arduino dependency, so it runs in native tests and can
// later drop into the display sketch beside the BLE and touch code. Nonblocking:
// call update() from loop(); every motion is a time-based slew toward a target.
#include <stddef.h>
#include <stdint.h>
#include "ScooperConfig.h"

namespace scooper {

enum class State : uint8_t {
  Relaxed,   // No PWM pulses: servos are limp. Also the state before begin().
  Holding,   // At target, holding torque.
  Moving,    // Single move (`set` or `home`) in progress.
  Sequence,  // Running squeeze or release steps.
};

inline const char* stateName(State state) {
  switch (state) {
    case State::Relaxed: return "relaxed";
    case State::Holding: return "holding";
    case State::Moving: return "moving";
    case State::Sequence: return "sequence";
  }
  return "?";
}

inline bool inLimits(uint8_t joint, int deg) {
  return deg >= kJoints[joint].minDeg && deg <= kJoints[joint].maxDeg;
}

template <size_t N>
constexpr uint8_t stepCount(const Step (&)[N]) { return static_cast<uint8_t>(N); }

// Out provides attach(joint), write(joint, servoDeg) and detach(joint).
template <class Out>
class Scooper {
 public:
  static constexpr uint32_t kMaxTickMs = 200;  // A stalled loop slows motion, never jumps.

  explicit Scooper(Out& out) : out_(out) {}

  // Checks the tables in ScooperConfig.h. Nothing moves until this passes.
  bool validate() {
    valid_ = false;
    for (uint8_t i = 0; i < kJointCount; ++i) {
      const Joint& j = kJoints[i];
      if (j.minDeg > j.maxDeg || j.maxDeg > 180 || !inLimits(i, j.homeDeg)) {
        return fail("a joint's limits or home angle are invalid");
      }
    }
    if (!stepsValid(kSqueezeSteps, stepCount(kSqueezeSteps)) ||
        !stepsValid(kReleaseSteps, stepCount(kReleaseSteps))) {
      return fail("a squeeze/release step angle is outside its joint's limits");
    }
    valid_ = true;
    return true;
  }

  // Starts pulses at the home pose. Servos jump there at power-up: keep hands clear.
  bool begin(uint32_t now) {
    if (!valid_ && !validate()) return false;
    for (uint8_t i = 0; i < kJointCount; ++i) pos_[i] = target_[i] = kJoints[i].homeDeg;
    attachAll();
    state_ = State::Holding;
    last_ = now;
    return true;
  }

  bool setJoint(uint8_t joint, int deg, uint16_t degPerSec) {
    if (joint >= kJointCount) return fail("no servo with that number");
    if (!inLimits(joint, deg)) return fail("angle is outside that servo's limits");
    if (!ready()) return false;
    target_[joint] = static_cast<float>(deg);
    speed_ = degPerSec;
    state_ = State::Moving;
    return true;
  }

  bool home() {
    if (!ready()) return false;
    for (uint8_t i = 0; i < kJointCount; ++i) target_[i] = kJoints[i].homeDeg;
    speed_ = kDefaultDegPerSec;
    state_ = State::Moving;
    return true;
  }

  bool squeeze() { return run(kSqueezeSteps, stepCount(kSqueezeSteps), "squeeze"); }
  bool release() { return run(kReleaseSteps, stepCount(kReleaseSteps), "release"); }

  // Freeze where the sticks are now and abandon any sequence. Keeps holding torque.
  void stop() {
    for (uint8_t i = 0; i < kJointCount; ++i) target_[i] = pos_[i];
    steps_ = nullptr;
    if (state_ != State::Relaxed) state_ = State::Holding;
  }

  // Stop pulses so the servos go limp and quiet. The next move restarts from the
  // last commanded angles, so a stick pushed by hand meanwhile will snap back.
  void relax() {
    stop();
    for (uint8_t i = 0; i < kJointCount; ++i) out_.detach(i);
    state_ = State::Relaxed;
  }

  void update(uint32_t now) {
    uint32_t dt = now - last_;
    last_ = now;
    if (dt > kMaxTickMs) dt = kMaxTickMs;
    if (state_ != State::Moving && state_ != State::Sequence) return;
    if (!slew(dt)) return;
    if (state_ == State::Moving) {
      state_ = State::Holding;
      return;
    }
    if (!arrived_) {
      arrived_ = true;
      arrivedAt_ = now;
    }
    if (now - arrivedAt_ < steps_[index_].holdMs) return;
    if (++index_ >= count_) {
      steps_ = nullptr;
      state_ = State::Holding;
      return;
    }
    loadStep();
  }

  State state() const { return state_; }
  int angle(uint8_t joint) const { return static_cast<int>(pos_[joint] + 0.5f); }
  int targetAngle(uint8_t joint) const { return static_cast<int>(target_[joint] + 0.5f); }
  const char* sequenceName() const { return steps_ ? sequence_ : ""; }
  const char* stepLabel() const { return steps_ ? steps_[index_].label : ""; }
  uint8_t stepIndex() const { return steps_ ? index_ : 0; }
  const char* error() const { return error_; }

 private:
  bool fail(const char* message) {
    error_ = message;
    return false;
  }

  static bool stepsValid(const Step* steps, uint8_t count) {
    for (uint8_t s = 0; s < count; ++s) {
      for (uint8_t i = 0; i < kJointCount; ++i) {
        const int deg = steps[s].deg[i];
        if (deg != kKeep && !inLimits(i, deg)) return false;
      }
    }
    return true;
  }

  // New commands wait for a running sequence to be stopped; a relaxed arm re-arms.
  bool ready() {
    if (!valid_) return fail("config invalid; fix ScooperConfig.h");
    if (state_ == State::Sequence) return fail("busy with a sequence; send stop first");
    if (state_ == State::Relaxed) attachAll();
    return true;
  }

  bool run(const Step* steps, uint8_t count, const char* name) {
    if (count == 0) return fail("sequence is empty");
    if (!ready()) return false;
    steps_ = steps;
    count_ = count;
    index_ = 0;
    sequence_ = name;
    state_ = State::Sequence;
    loadStep();
    return true;
  }

  void loadStep() {
    const Step& step = steps_[index_];
    for (uint8_t i = 0; i < kJointCount; ++i) {
      if (step.deg[i] != kKeep) target_[i] = step.deg[i];
    }
    speed_ = step.degPerSec;
    arrived_ = false;
  }

  // Moves each joint at most speed * dt toward its target. True once all arrived.
  bool slew(uint32_t dt) {
    const float maxStep = speed_ == 0 ? 180.0f : speed_ * (dt / 1000.0f);
    bool done = true;
    for (uint8_t i = 0; i < kJointCount; ++i) {
      const float diff = target_[i] - pos_[i];
      if (diff == 0) continue;
      if (diff > maxStep) pos_[i] += maxStep;
      else if (diff < -maxStep) pos_[i] -= maxStep;
      else pos_[i] = target_[i];
      out_.write(i, servoDeg(i));
      if (pos_[i] != target_[i]) done = false;
    }
    return done;
  }

  void attachAll() {
    for (uint8_t i = 0; i < kJointCount; ++i) {
      out_.attach(i);
      out_.write(i, servoDeg(i));
    }
  }

  // Logical angle -> servo angle: mirror if inverted, then add the horn trim.
  // The output layer clamps the result to the servo's 0-180 range.
  float servoDeg(uint8_t joint) const {
    const float deg = kJoints[joint].inverted ? 180.0f - pos_[joint] : pos_[joint];
    return deg + kJoints[joint].trimDeg;
  }

  Out& out_;
  State state_ = State::Relaxed;
  bool valid_ = false;
  float pos_[kJointCount] = {};
  float target_[kJointCount] = {};
  uint16_t speed_ = kDefaultDegPerSec;
  uint32_t last_ = 0;
  const Step* steps_ = nullptr;
  uint8_t count_ = 0, index_ = 0;
  const char* sequence_ = "";
  bool arrived_ = false;
  uint32_t arrivedAt_ = 0;
  const char* error_ = "";
};

}  // namespace scooper
