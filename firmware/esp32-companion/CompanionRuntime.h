#pragma once
#include <Arduino.h>
#include <esp_random.h>
#include "CompanionBle.h"
#include "ArduinoServoPwm.h"

namespace companion {
class Runtime {
 public:
  Runtime() : servos_(pwm_), control_(servos_) {}
  CommandMailbox mailbox;
  void begin() {
    bool output = control_.begin(millis(), esp_random());
    output &= servos_.initialized();
    control_.outputReady(output);
    if (!output) Serial.println("Scooper PWM failed; motion disabled until restart.");
    Serial.println("Combined BLE display + scooper ready. Serial: status, pwm, stop, relax, home, squeeze, release, set 1|2 degrees [speed].");
    printStatus();
  }
  void poll(uint32_t now) {
    if (havePoll_) { const uint32_t gap = now - lastPoll_; if (gap > maxLoopGap_) maxLoopGap_ = gap; }
    havePoll_ = true; lastPoll_ = now;
    readSerial(now); updateMotion(millis());
  }
  // Main loop only. Epoch fencing handles a disconnect/reconnect between ticks.
  void link(bool connected, uint32_t epoch, uint32_t now) {
    if (epoch != epoch_) { control_.connectionLost(); epoch_ = epoch; }
    connected_ = connected; updateMotion(now);
  }
  void updateMotion(uint32_t now) {
    control_.outputReady(servos_.healthy());
    if (control_.watchdogStopped() && reportedStop_ != control_.stopReason()) {
      reportedStop_ = control_.stopReason();
      Serial.printf("Scooper stopped: %s control_age_ms=%lu max_loop_gap_ms=%lu\n", stopReasonName(reportedStop_),
        static_cast<unsigned long>(control_.contactAge(now)), static_cast<unsigned long>(maxLoopGap_));
    } else if (!control_.watchdogStopped()) reportedStop_ = StopReason::None;
    control_.tick(now, connected_);
    control_.outputReady(servos_.healthy());
  }
  Result execute(const CommandFrame& frame, uint32_t now) {
    control_.outputReady(servos_.healthy());
    const Result result = apply(control_, frame, now);
    control_.outputReady(servos_.healthy());
    const Command command = decodeCommand(frame).command;
    if (result == Result::Applied && !control_.servoReady() && command != Command::Status && command != Command::Stop) return Result::Refused;
    return result;
  }
  void status(uint8_t* out, Result result = Result::Status, uint32_t reply = 0) const { encodeStatus(out, control_, result, reply); }
 private:
  void printStatus() const {
    const auto& arm = control_.arm();
    Serial.printf("BLE scooper boot=%lu seq=%lu ready=%d state=%s left=%d right=%d watchdog=%d\n",
      static_cast<unsigned long>(control_.order().boot()), static_cast<unsigned long>(control_.order().sequence()),
      control_.servoReady(), scooper::stateName(arm.state()), arm.angle(0), arm.angle(1), control_.watchdogStopped());
    Serial.printf("Diagnostics: stop_reason=%s control_age_ms=%lu max_loop_gap_ms=%lu PWM_fault=%s\n",
      stopReasonName(control_.stopReason()), static_cast<unsigned long>(control_.contactAge(millis())),
      static_cast<unsigned long>(maxLoopGap_), pwmFaultName(servos_.fault()));
  }
  void serialCommand() {
    line_[lineLength_] = '\0';
    char* name = strtok(line_, " \t"); if (!name) return;
    if (!strcmp(name, "status")) { printStatus(); return; }
    if (!strcmp(name, "pwm")) {
      for (uint8_t i = 0; i < scooper::kJointCount; ++i) {
        const uint8_t pin = scooper::kJoints[i].pin;
        Serial.printf("PWM %s GPIO%u: frequency_hz=%lu duty=%lu / 16384\n", scooper::kJoints[i].name, pin,
          static_cast<unsigned long>(ledcReadFreq(pin)), static_cast<unsigned long>(ledcRead(pin)));
      }
      Serial.println("Readback is PWM register state, not measured shaft position. Wait >20 ms after a move before reading.");
      return;
    }
    if (!strcmp(name, "x")) name = const_cast<char*>("stop");
    if (!strcmp(name, "set")) {
      uint32_t joint = 0, degrees = 0, speed = scooper::kDefaultDegPerSec;
      const char* speedText;
      if (!unsigned32(strtok(nullptr, " \t"), joint) || !unsigned32(strtok(nullptr, " \t"), degrees) ||
          ((speedText = strtok(nullptr, " \t")) && !unsigned32(speedText, speed)) || strtok(nullptr, " \t") ||
          joint < 1 || joint > 2 || degrees > 180 || speed < 1 || speed > 600 || !control_.servoReady()) { Serial.println("error: set 1|2 degrees [speed 1-600]"); return; }
      if (!control_.setJoint(joint - 1, static_cast<int>(degrees), speed)) Serial.printf("error: %s\n", control_.arm().error());
      else Serial.println("ok");
      return;
    }
    if (strtok(nullptr, " \t")) { Serial.println("error: unexpected arguments"); return; }
    const char* error = control_.local(commandFrom(name), millis());
    if (error) Serial.printf("error: %s\n", error); else { Serial.println("ok"); printStatus(); }
  }
  void readSerial(uint32_t now) {
    if (lineLength_ && static_cast<uint32_t>(now - lineStarted_) >= 2000) { lineLength_ = 0; lineOverflow_ = true; }
    for (int budget = 128; budget > 0 && Serial.available(); --budget) {
      const char c = static_cast<char>(Serial.read());
      if (c == '\r' || c == '\n') {
        if (lineOverflow_) Serial.println("error: oversized, invalid or expired serial line");
        else if (lineLength_) serialCommand();
        lineLength_ = 0; lineOverflow_ = false;
      } else if (c < 32 || c > 126 || lineLength_ == sizeof(line_) - 1) lineOverflow_ = true;
      else if (!lineOverflow_) { if (!lineLength_) lineStarted_ = now; line_[lineLength_++] = c; }
    }
  }
  ArduinoServoPwm pwm_;
  ServoPwm<ArduinoServoPwm> servos_;
  Control<ServoPwm<ArduinoServoPwm>> control_;
  char line_[64] = {};
  size_t lineLength_ = 0;
  uint32_t lineStarted_ = 0, epoch_ = 0;
  uint32_t lastPoll_ = 0, maxLoopGap_ = 0;
  StopReason reportedStop_ = StopReason::None;
  bool havePoll_ = false;
  bool connected_ = false, lineOverflow_ = false;
};
}  // namespace companion
