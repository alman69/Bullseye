#pragma once
#include "CompanionHttp.h"
#include "Scooper.h"

namespace companion {
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
  const char* execute(const Request& request, uint32_t now) {
    if (request.error) return "unsupported request";
    if (request.command == Command::Status) { lastContact_ = now; return nullptr; }
    if (!order_.accept(request.boot, request.sequence)) return "old command or board restarted; reconnect";
    lastContact_ = now;
    return run(request.command, now, true);
  }
  const char* local(Command command, uint32_t now) { return run(command, now, false); }
  bool setJoint(uint8_t joint, int degrees, uint16_t speed) {
    if (!servoReady_ || !arm_.setJoint(joint, degrees, speed)) return false;
    order_.fence(); wirelessMotion_ = false; return true;
  }
  void connectionLost() { if (wirelessMotion_) stopWireless(); }
  void tick(uint32_t now, bool link) {
    if (linkWasConnected_ && !link) connectionLost();
    linkWasConnected_ = link;
    const bool moving = arm_.state() == scooper::State::Moving || arm_.state() == scooper::State::Sequence;
    if (wirelessMotion_ && moving && static_cast<uint32_t>(now - lastContact_) >= 2000) stopWireless();
    arm_.update(now);
  }
 private:
  void stopWireless() { arm_.stop(); order_.fence(); wirelessMotion_ = false; watchdogStopped_ = true; }
  const char* run(Command command, uint32_t now, bool wireless) {
    if (command == Command::Stop || command == Command::Relax) {
      if (command == Command::Stop) arm_.stop(); else arm_.relax();
      wirelessMotion_ = false;
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
    wirelessMotion_ = wireless; watchdogStopped_ = false; lastContact_ = now;
    return nullptr;
  }
  scooper::Scooper<Out> arm_;
  CommandOrder order_;
  uint32_t lastContact_ = 0;
  bool servoReady_ = false, wirelessMotion_ = false, watchdogStopped_ = false, linkWasConnected_ = false;
};
}  // namespace companion
