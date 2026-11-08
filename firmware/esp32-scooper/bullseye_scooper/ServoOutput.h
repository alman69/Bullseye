#pragma once
// 50 Hz hobby-servo PWM on the ESP32 LEDC peripheral, using the Espressif Arduino
// core 3.x pin-based API (ledcAttach/ledcWrite/ledcDetach). No ESP32Servo library.
#include <Arduino.h>
#include "ScooperConfig.h"

namespace scooper {

class LedcServos {
 public:
  static constexpr uint8_t kBits = 14;  // 20 ms period / 16384 counts = ~1.2 us per count.

  void attach(uint8_t joint) {
    if (on_[joint]) return;
    on_[joint] = ledcAttach(kJoints[joint].pin, kServoHz, kBits);
    if (!on_[joint]) Serial.printf("PWM attach failed on GPIO%u\n", kJoints[joint].pin);
  }

  void write(uint8_t joint, float deg) {
    if (!on_[joint]) return;
    if (deg < 0) deg = 0;
    if (deg > 180) deg = 180;
    const float us = kPulseMinUs + (kPulseMaxUs - kPulseMinUs) * deg / 180.0f;
    const uint32_t duty = static_cast<uint32_t>(us * (1UL << kBits) * kServoHz / 1000000.0f + 0.5f);
    ledcWrite(kJoints[joint].pin, duty);
  }

  // Drive the line low afterwards so a floating pin cannot twitch the servo.
  void detach(uint8_t joint) {
    if (!on_[joint]) return;
    ledcDetach(kJoints[joint].pin);
    pinMode(kJoints[joint].pin, OUTPUT);
    digitalWrite(kJoints[joint].pin, LOW);
    on_[joint] = false;
  }

 private:
  bool on_[kJointCount] = {};
};

}  // namespace scooper
