#pragma once
#include <cmath>
#include "ScooperConfig.h"

namespace companion {
enum class PwmFault : uint8_t { None, Attach, Write, Detach, Argument };
inline const char* pwmFaultName(PwmFault fault) {
  switch (fault) {
    case PwmFault::Attach: return "attach";
    case PwmFault::Write: return "write";
    case PwmFault::Detach: return "detach";
    case PwmFault::Argument: return "invalid argument";
    default: return "none";
  }
}

// Actual HAL return values establish readiness, not an immediate duty/frequency
// readback. LEDC applies a new duty at the next PWM cycle (20 ms at 50 Hz).
// Used by the uploadable sketch and regression checks with delayed-readback HAL.
template<class Hal> class ServoPwm {
 public:
  static constexpr uint8_t kBits = 14;
  explicit ServoPwm(Hal& hal) : hal_(hal) {}
  void attach(uint8_t joint) {
    if (!valid(joint) || !healthy() || on_[joint]) return;
    if (!hal_.attach(scooper::kJoints[joint].pin, scooper::kServoHz, kBits)) { fail(PwmFault::Attach, joint); return; }
    on_[joint] = true; written_[joint] = false;
  }
  void write(uint8_t joint, float degrees) {
    if (!valid(joint) || !healthy()) return;
    if (!on_[joint] || !std::isfinite(degrees)) { fail(PwmFault::Argument, joint); return; }
    if (degrees < 0) degrees = 0;
    if (degrees > 180) degrees = 180;
    const float us = scooper::kPulseMinUs + (scooper::kPulseMaxUs - scooper::kPulseMinUs) * degrees / 180.0f;
    const uint32_t duty = static_cast<uint32_t>(us * (1UL << kBits) * scooper::kServoHz / 1000000.0f + 0.5f);
    if (!hal_.write(scooper::kJoints[joint].pin, duty)) { fail(PwmFault::Write, joint); return; }
    written_[joint] = true;
  }
  void detach(uint8_t joint) {
    if (!valid(joint) || !on_[joint]) return;
    if (!hal_.detach(scooper::kJoints[joint].pin)) { fail(PwmFault::Detach, joint); return; }
    on_[joint] = written_[joint] = false;
    hal_.low(scooper::kJoints[joint].pin);
  }
  bool healthy() const { return fault_ == PwmFault::None; }
  bool initialized() const {
    if (!healthy()) return false;
    for (uint8_t i = 0; i < scooper::kJointCount; ++i) if (!on_[i] || !written_[i]) return false;
    return true;
  }
  PwmFault fault() const { return fault_; }
  uint8_t faultJoint() const { return faultJoint_; }
 private:
  bool valid(uint8_t joint) { if (joint < scooper::kJointCount) return true; fail(PwmFault::Argument, joint); return false; }
  void fail(PwmFault fault, uint8_t joint) {
    if (healthy()) { fault_ = fault; faultJoint_ = joint; hal_.error(pwmFaultName(fault), joint); }
    // Roll back both outputs on an actual HAL error. Reconnect never rearms a
    // failed PWM output; recovery requires reset and fresh initialization.
    for (uint8_t i = 0; i < scooper::kJointCount; ++i) {
      if (!on_[i]) continue;
      hal_.detach(scooper::kJoints[i].pin); hal_.low(scooper::kJoints[i].pin);
      on_[i] = written_[i] = false;
    }
  }
  Hal& hal_;
  PwmFault fault_ = PwmFault::None;
  uint8_t faultJoint_ = 255;
  bool on_[scooper::kJointCount] = {}, written_[scooper::kJointCount] = {};
};
}  // namespace companion
