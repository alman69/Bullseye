#pragma once
#include <Arduino.h>
#include "ServoPwm.h"

namespace companion {
class ArduinoServoPwm {
 public:
  bool attach(uint8_t pin, uint32_t hz, uint8_t bits) { return ledcAttach(pin, hz, bits); }
  bool write(uint8_t pin, uint32_t duty) { return ledcWrite(pin, duty); }
  bool detach(uint8_t pin) { return ledcDetach(pin); }
  void low(uint8_t pin) { pinMode(pin, OUTPUT); digitalWrite(pin, LOW); }
  void error(const char* stage, uint8_t joint) {
    if (joint < scooper::kJointCount) Serial.printf("Servo PWM %s failed: %s GPIO%u. Both outputs disabled.\n", stage, scooper::kJoints[joint].name, scooper::kJoints[joint].pin);
    else Serial.printf("Servo PWM %s failed: invalid joint %u. Both outputs disabled.\n", stage, joint);
  }
};
}  // namespace companion
