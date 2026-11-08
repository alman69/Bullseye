#include <assert.h>
#include <string.h>
#include <string>
#include <iostream>
#include "CompanionHttp.h"
#include "CompanionControl.h"
#include "CompanionBle.h"
#include "ServoPwm.h"
#include "DisplayJson.h"
#include "DisplayRenderer.h"

using namespace companion;

Request decode(const std::string& text, size_t chunk = 1) {
  HttpDecoder decoder; decoder.reset(100);
  for (size_t offset = 0; offset < text.size(); offset += chunk) {
    decoder.feed(reinterpret_cast<const uint8_t*>(text.data() + offset), std::min(chunk, text.size() - offset));
  }
  assert(decoder.ready()); return decoder.decode();
}
void httpChecks() {
  const std::string status = "GET /status HTTP/1.1\r\nHost: 192.168.4.50\r\n\r\n";
  const std::string command = "POST /command?name=squeeze&boot=42&seq=1 HTTP/1.1\r\nHost: 192.168.4.50\r\nContent-Length: 0\r\n\r\n";
  assert(decode(status).command == Command::Status && decode(status).error == 0);
  for (size_t size : {1, 3, 7, 16, 256}) {
    const auto request = decode(command, size);
    assert(request.error == 0 && request.command == Command::Squeeze && request.boot == 42 && request.sequence == 1);
  }
  for (const std::string& name : {"squeeze", "release", "home", "stop", "relax"}) {
    assert(decode("POST /command?seq=12&boot=42&name=" + name + " HTTP/1.1\r\nContent-Length: 000\r\n\r\n").error == 0);
  }
  assert(decode("GET /command?name=squeeze&boot=42&seq=1 HTTP/1.1\r\n\r\n").error == 405);
  assert(decode("POST /command?name=squeeze HTTP/1.1\r\n\r\n").error == 409);
  assert(decode("POST /command?name=squeeze&boot=42&seq=0 HTTP/1.1\r\n\r\n").error == 409);
  assert(decode("POST /command?name=squeeze&boot=42&seq=4294967296 HTTP/1.1\r\n\r\n").error == 400);
  assert(decode("POST /command?name=squeeze&boot=42&seq=1&seq=2 HTTP/1.1\r\n\r\n").error == 400);
  assert(decode("POST /command?name=invalid&boot=42&seq=1 HTTP/1.1\r\n\r\n").error == 400);
  assert(decode("GET /status HTTP/1.1\r\nContent-Length: 1\r\n\r\nX").error == 413);
  assert(decode("GET /status HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n").error == 400);
  assert(decode("GET /status HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n").error == 400);
  assert(decode("GET /not-found HTTP/1.1\r\n\r\n").error == 404);
  assert(decode("GET /status HTTP/2.0\r\n\r\n").error == 400);
  assert(decode(std::string(1536, 'x')).error == 431);
  assert(decode(std::string("GET /status\0", 12)).error == 400);
  HttpDecoder partial; partial.reset(UINT32_MAX - 100);
  partial.feed(reinterpret_cast<const uint8_t*>("POST /"), 6);
  assert(!partial.ready() && !partial.expired(398) && partial.expired(399));
}

struct Servos {
  bool attached[2] = {}; float angle[2] = {};
  void attach(uint8_t joint) { assert(joint < 2); attached[joint] = true; }
  void detach(uint8_t joint) { assert(joint < 2); attached[joint] = false; }
  void write(uint8_t joint, float value) { assert(joint < 2 && attached[joint] && value >= 0 && value <= 180); angle[joint] = value; }
};
// Models the installed LEDC core: a successful write becomes readable only at
// the next 20 ms cycle. The old immediate ledcReadFreq readiness gate fails here.
struct DelayedPwm {
  bool on[43] = {}; uint32_t pending[43] = {}, applied[43] = {};
  int failAttach = -1, failWrite = -1, failDetach = -1, errors = 0;
  const char* stage = "";
  bool attach(uint8_t pin, uint32_t hz, uint8_t bits) {
    assert((pin == 41 || pin == 42) && hz == 50 && bits == 14);
    if (pin == failAttach) return false;
    on[pin] = true; return true;
  }
  bool write(uint8_t pin, uint32_t duty) {
    assert(on[pin] && duty >= 410 && duty <= 1966);
    if (pin == failWrite) return false;
    pending[pin] = duty; return true;
  }
  bool detach(uint8_t pin) { if (pin == failDetach) return false; on[pin] = false; return true; }
  void low(uint8_t pin) { on[pin] = false; pending[pin] = applied[pin] = 0; }
  void error(const char* name, uint8_t) { ++errors; stage = name; }
  uint32_t readFrequency(uint8_t pin) const { return on[pin] && applied[pin] ? 50 : 0; }
  void nextCycle() { for (uint8_t pin : {41, 42}) if (on[pin]) applied[pin] = pending[pin]; }
};
void pwmChecks() {
  DelayedPwm hal; ServoPwm<DelayedPwm> output(hal); Control<ServoPwm<DelayedPwm>> control(output);
  assert(control.begin(0, 42));
  assert(hal.readFrequency(41) == 0 && hal.readFrequency(42) == 0); // Old code falsely failed startup.
  control.outputReady(output.initialized());
  assert(control.servoReady() && output.healthy() && hal.errors == 0);
  assert(hal.pending[41] == 1188 && hal.pending[42] == 1188);
  hal.nextCycle(); assert(hal.readFrequency(41) == 50 && hal.readFrequency(42) == 50);
  assert(control.local(Command::Relax, 20) == nullptr && output.healthy());
  assert(!hal.on[41] && !hal.on[42] && control.servoReady());
  assert(control.local(Command::Home, 30) == nullptr && output.initialized());
  output.write(0, -10); assert(hal.pending[41] == 410);
  output.write(0, 190); assert(hal.pending[41] == 1966);
  hal.failWrite = 42; output.write(1, 100);
  assert(!output.healthy() && output.fault() == PwmFault::Write && output.faultJoint() == 1);
  assert(!hal.on[41] && !hal.on[42] && !strcmp(hal.stage, "write"));
  control.outputReady(output.healthy()); assert(!control.servoReady() && control.arm().state() == scooper::State::Relaxed);
  output.attach(0); assert(!hal.on[41] && hal.errors == 1); // Never silently rearm.
  DelayedPwm attachHal; attachHal.failAttach = 42;
  ServoPwm<DelayedPwm> attachFailure(attachHal); Control<ServoPwm<DelayedPwm>> failed(attachFailure);
  failed.begin(0, 43); failed.outputReady(attachFailure.initialized());
  assert(!failed.servoReady() && !attachHal.on[41] && !attachHal.on[42] && attachFailure.fault() == PwmFault::Attach);
  DelayedPwm detachHal; ServoPwm<DelayedPwm> detaching(detachHal);
  detaching.attach(0); detaching.write(0, 90); detachHal.failDetach = 41; detaching.detach(0);
  assert(!detaching.healthy() && detaching.fault() == PwmFault::Detach && !detachHal.on[41]);
  DelayedPwm nanHal; ServoPwm<DelayedPwm> nanOutput(nanHal); nanOutput.attach(0); nanOutput.write(0, NAN);
  assert(!nanOutput.healthy() && !nanHal.on[41] && nanOutput.fault() == PwmFault::Argument);
}
Request request(Command command, uint32_t sequence, uint32_t boot = 42) { return {command, boot, sequence, 0}; }
CommandFrame bleFrame(uint8_t opcode, uint32_t sequence, uint32_t epoch = 1) {
  CommandFrame frame; frame.epoch = epoch;
  frame.bytes[0] = 'B'; frame.bytes[1] = 'C'; frame.bytes[2] = 1; frame.bytes[3] = opcode;
  write32(frame.bytes + 4, 42); write32(frame.bytes + 8, sequence); return frame;
}
void bleChecks() {
  Servos output; Control<Servos> control(output); control.begin(0, 42);
  CommandMailbox mailbox;
  const auto move = bleFrame(1, 1), stop = bleFrame(4, 3);
  const uint8_t golden[] = {0x42,0x43,1,1,42,0,0,0,1,0,0,0};
  assert(!memcmp(move.bytes, golden, sizeof(golden)));
  assert(decodeCommand(move).command == Command::Squeeze);
  assert(apply(control, move, 0) == Result::Applied);
  control.tick(1000, true);
  assert(apply(control, stop, 1000) == Result::Applied);
  uint8_t bytes[20]; encodeStatus(bytes, control, Result::Applied, 3);
  assert(bytes[0] == 'B' && bytes[1] == 'C' && bytes[2] == 1 && bytes[3] == 1);
  assert(read32(bytes + 4) == 42 && read32(bytes + 8) == 3 && read32(bytes + 12) == 3);
  assert(bytes[16] == 5 && bytes[17] == control.arm().angle(0) && bytes[19] == 0);
  assert(apply(control, move, 1001) == Result::Old);
  assert(apply(control, bleFrame(6, 4), 1002) == Result::Invalid);
  auto wrong = bleFrame(1, 4); write32(wrong.bytes + 4, 43);
  assert(apply(control, wrong, 1002) == Result::Old);
  mailbox.feed(move.bytes, 5, 1); assert(mailbox.takeError() == Result::Invalid);
  mailbox.feed(move.bytes, 12, 1); mailbox.feed(move.bytes, 12, 1);
  mailbox.feed(move.bytes, 12, 1); assert(mailbox.takeError() == Result::Overflow);
  mailbox.feed(stop.bytes, 12, 1); CommandFrame received;
  assert(mailbox.pop(received) && decodeCommand(received).command == Command::Stop && !mailbox.pop(received));
  mailbox.feed(move.bytes, 12, 1); mailbox.reset(); assert(!mailbox.pop(received));
  assert(apply(control, bleFrame(1, 4), 1100) == Result::Applied);
  control.tick(1200, true); const auto angle = control.arm().angle(0);
  control.connectionLost(); control.tick(1300, true);
  assert(control.watchdogStopped() && control.arm().angle(0) == angle);
  assert(apply(control, bleFrame(1, 6), 1400) == Result::Applied);
  // Display JSON traffic by itself cannot keep remote scooper movement alive.
  control.tick(3400, true); assert(control.watchdogStopped());
  assert(apply(control, bleFrame(1, 8), 3500) == Result::Applied);
  for (uint32_t now = 3500; now <= 8000; now += 100) {
    if (now % 500 == 0) assert(apply(control, bleFrame(0, 0), now) == Result::Applied);
    control.tick(now, true);
  }
  assert(control.arm().angle(0) == scooper::kInDeg && !control.watchdogStopped());
}
void orderAndTimeoutChecks() {
  Servos output; Control<Servos> control(output);
  assert(control.begin(0, 42)); control.tick(0, true);
  assert(control.execute(request(Command::Squeeze, 1), 10) == nullptr);
  control.tick(200, true);
  assert(control.execute(request(Command::Stop, 3), 210) == nullptr);
  const auto angle = control.arm().angle(0);
  assert(control.execute(request(Command::Squeeze, 2), 220) != nullptr);
  assert(control.execute(request(Command::Squeeze, 3), 230) != nullptr);
  assert(control.execute(request(Command::Squeeze, 4, 100), 240) != nullptr);
  control.tick(1000, true); assert(control.arm().angle(0) == angle);
  assert(control.execute(request(Command::Squeeze, 4), 1010) == nullptr);
  control.tick(1500, true); control.tick(3010, true);
  assert(control.watchdogStopped() && control.arm().state() == scooper::State::Holding);
  assert(control.order().sequence() == 5 && control.execute(request(Command::Home, 5), 3020));
  assert(control.execute(request(Command::Home, 6), 3100) == nullptr);
  control.tick(3110, false); assert(control.watchdogStopped() && control.order().sequence() == 7);
  control.outputReady(false);
  assert(control.arm().state() == scooper::State::Relaxed);
  assert(control.execute(request(Command::Squeeze, 8), 3120));
  assert(control.execute(request(Command::Stop, 9), 3130) == nullptr);
  // Local USB calibration does not require a remote heartbeat.
  Servos localOutput; Control<Servos> local(localOutput); local.begin(0, 1);
  assert(local.local(Command::Squeeze, 0) == nullptr);
  for (uint32_t now = 10; now < 6000; now += 10) local.tick(now, false);
  assert(local.arm().state() == scooper::State::Holding && local.arm().angle(0) == scooper::kInDeg);
  CommandOrder wrapped; wrapped.begin(9); assert(wrapped.accept(9, UINT32_MAX)); wrapped.fence();
  assert(wrapped.sequence() == UINT32_MAX && !wrapped.accept(9, 1));
}
struct Screen {
  int writes = 0;
  void fillScreen(uint16_t) { ++writes; }
  void setTextWrap(bool) {}
  void setTextColor(uint16_t) {}
  void setTextSize(int) {}
  void setCursor(int, int) {}
  void print(const char*) { assert(false && "Default face is full-screen and has no text"); }
  void fillRect(int x, int y, int w, int h, uint16_t) { assert(x >= 0 && y >= 0 && x + w <= 480 && y + h <= 320); ++writes; }
};
void sharedLoopChecks() {
  Servos output; Control<Servos> control(output); control.begin(0, 42);
  Servos lostOutput; Control<Servos> lostHttp(lostOutput); lostHttp.begin(0, 7);
  bullseye::Model telemetry; telemetry.link(true, 1, 0);
  bullseye::Renderer face; Screen screen;
  HttpDecoder slow; slow.reset(0); slow.feed(reinterpret_cast<const uint8_t*>("POST /"), 6);
  uint32_t sequence = 0; int acks = 0; const int firstWrites = screen.writes;
  assert(control.execute(request(Command::Squeeze, 1), 0) == nullptr);
  assert(lostHttp.execute(request(Command::Squeeze, 1, 7), 0) == nullptr);
  for (uint32_t now = 0; now <= 5000; now += 10) {
    // An incomplete network peer never blocks the main loop.
    if (slow.expired(now)) slow.reset(now);
    if (now % 500 == 0) {
      assert(control.execute(request(Command::Status, 0), now) == nullptr);
      char json[200]; snprintf(json, sizeof(json), "{\"v\":1,\"seq\":%lu,\"distance_cm\":31,\"status\":\"live\",\"sample_age_ms\":0,\"rover_connected\":true,\"source\":\"rover\"}\n", static_cast<unsigned long>(++sequence));
      bullseye::Receiver receiver;
      for (size_t offset = 0; offset < strlen(json); offset += 13) receiver.feed(reinterpret_cast<const uint8_t*>(json + offset), std::min<size_t>(13, strlen(json) - offset), now, 1);
      bullseye::Frame frame; bullseye::Sample sample;
      assert(receiver.pop(frame) && bullseye::decodeSample(frame, sample));
      assert(telemetry.apply(sample, frame, now) == bullseye::Ack::Applied); ++acks;
    }
    control.tick(now, true);
    lostHttp.tick(now, true);  // BLE remains fresh, but this controller gets no HTTP contact.
    if (now % 100 == 0) face.render(screen, telemetry, "GT911", now);
  }
  assert(acks == 11 && telemetry.state(5000) == bullseye::ViewState::Live);
  assert(screen.writes > firstWrites + 100);
  assert(control.arm().state() == scooper::State::Holding && control.arm().angle(0) == scooper::kInDeg);
  assert(lostHttp.watchdogStopped() && lostHttp.arm().angle(0) < scooper::kInDeg);
  assert(telemetry.face.mood(5000) == bullseye::Mood::Curious);
}
int main() {
  httpChecks(); pwmChecks(); bleChecks(); orderAndTimeoutChecks(); sharedLoopChecks();
  std::cout << "Combined companion checks passed: delayed PWM readback, HAL faults, BLE commands/status, bounded mailboxes, ordered STOP, watchdog, telemetry, face animation and servo updates.\n";
}
