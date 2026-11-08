#pragma once
#include "CompanionHttp.h"
#include "Scooper.h"

namespace companion {
enum class StopReason : uint8_t { None, ContactTimeout, LinkLost };
inline const char* stopReasonName(StopReason reason) {
  return reason == StopReason::ContactTimeout ? "control-heartbeat-timeout" : reason == StopReason::LinkLost ? "BLE-link-loss" : "none";
}
// Shared by the actual sketch and native integration checks. Only this main-loop
// controller changes servo targets; telemetry alone never renews motion control.
template <class Out> class Control {
 public:
  explicit Control(Out& out) : arm_(out) {}
  bool begin(uint32_t now, uint32_t boot) {
    order_.begin(boot); servoReady_ = arm_.begin(now); return servoReady_;
  }
  void outputReady(bool ready) { servoReady_ &= ready; if (!servoReady_) arm_.relax(); }
  const scooper::Scooper<Out>& arm() const { return arm_; }
  const CommandOrder& order() const { return order_; }
  bool servoReady() const { return servoReady_; }
  bool watchdogStopped() const { return watchdogStopped_; }
  StopReason stopReason() const { return stopReason_; }
  uint32_t contactAge(uint32_t now) const { return contactSeen_ ? static_cast<uint32_t>(now - lastContact_) : UINT32_MAX; }
  const char* execute(const Request& request, uint32_t now) {
    if (request.error) return "unsupported request";
    if (request.command == Command::Status) { lastContact_ = now; contactSeen_ = true; return nullptr; }
    if (!order_.accept(request.boot, request.sequence)) return "old command or board restarted; reconnect";
    lastContact_ = now; contactSeen_ = true;
    return run(request.command, now, true);
  }
  const char* local(Command command, uint32_t now) { return run(command, now, false); }
  bool setJoint(uint8_t joint, int degrees, uint16_t speed) {
    if (!servoReady_ || !arm_.setJoint(joint, degrees, speed)) return false;
    order_.fence(); wirelessMotion_ = false; watchdogStopped_ = false; stopReason_ = StopReason::None; return true;
  }
  void connectionLost() {
    if (wirelessMotion_ && moving()) stopWireless(StopReason::LinkLost);
    else wirelessMotion_ = false;
  }
  void tick(uint32_t now, bool link) {
    if (linkWasConnected_ && !link) connectionLost();
    linkWasConnected_ = link;
    if (wirelessMotion_ && moving() && static_cast<uint32_t>(now - lastContact_) >= 2000) stopWireless(StopReason::ContactTimeout);
    arm_.update(now);
    if (!moving()) wirelessMotion_ = false;
  }
 private:
  bool moving() const { return arm_.state() == scooper::State::Moving || arm_.state() == scooper::State::Sequence; }
  void stopWireless(StopReason reason) { arm_.stop(); order_.fence(); wirelessMotion_ = false; watchdogStopped_ = true; stopReason_ = reason; }
  const char* run(Command command, uint32_t now, bool wireless) {
    if (command == Command::Stop || command == Command::Relax) {
      if (command == Command::Stop) arm_.stop(); else arm_.relax();
      wirelessMotion_ = false;
      watchdogStopped_ = false; stopReason_ = StopReason::None;
      if (!wireless) order_.fence();
      return nullptr;
    }
    if (!servoReady_) return "servo PWM initialization failed; inspect pins and restart";
    bool ok = false;
    if (command == Command::Squeeze) ok = arm_.squeeze();
    else if (command == Command::Release) ok = arm_.release();
    else if (command == Command::Home) ok = arm_.home();
    else return "unknown command";
    if (!ok) return arm_.error();
    wirelessMotion_ = wireless; watchdogStopped_ = false; stopReason_ = StopReason::None;
    if (wireless) { lastContact_ = now; contactSeen_ = true; }
    return nullptr;
  }
  scooper::Scooper<Out> arm_;
  CommandOrder order_;
  uint32_t lastContact_ = 0;
  StopReason stopReason_ = StopReason::None;
  bool contactSeen_ = false;
  bool servoReady_ = false, wirelessMotion_ = false, watchdogStopped_ = false, linkWasConnected_ = false;
};
}  // namespace companion
