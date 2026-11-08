#pragma once
#include "CompanionControl.h"

namespace companion {
constexpr const char* kCommandUuid = "3f7c1003-6f54-4a9c-8b2d-56e39a18c201";
constexpr const char* kStatusUuid = "3f7c1004-6f54-4a9c-8b2d-56e39a18c201";
enum class Result : uint8_t { Status, Applied, Invalid, Old, Refused, Overflow };
inline uint32_t read32(const uint8_t* p) {
  return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
         (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}
inline void write32(uint8_t* p, uint32_t value) { for (uint8_t i = 0; i < 4; ++i) p[i] = static_cast<uint8_t>(value >> (8 * i)); }
struct CommandFrame { uint8_t bytes[12] = {}; uint32_t epoch = 0; };
inline Request decodeCommand(const CommandFrame& frame) {
  const auto* p = frame.bytes;
  if (p[0] != 'B' || p[1] != 'C' || p[2] != 1 || p[3] > 5) return {};
  const Command commands[] = {Command::Status, Command::Squeeze, Command::Release, Command::Home, Command::Stop, Command::Relax};
  const uint32_t boot = read32(p + 4), seq = read32(p + 8);
  if (!boot || (p[3] == 0 ? seq != 0 : seq == 0)) return {};
  return {commands[p[3]], boot, seq, 0};
}
// Called only under the shared state mutex. A fixed-size write fits even MTU 23.
// Callbacks copy bytes; the main loop validates commands and controls the servos.
class CommandMailbox {
 public:
  void reset() { count_ = 0; error_ = Result::Status; }
  void feed(const uint8_t* bytes, size_t count, uint32_t epoch) {
    if (count != 12) { error_ = Result::Invalid; return; }
    if (count_ == 2) {
      if (bytes[0] == 'B' && bytes[1] == 'C' && bytes[2] == 1 && bytes[3] == 4) count_ = 0;
      else { error_ = Result::Overflow; return; }
    }
    memcpy(frames_[count_].bytes, bytes, 12); frames_[count_++].epoch = epoch;
  }
  bool pop(CommandFrame& frame) {
    if (!count_) return false;
    frame = frames_[0]; if (--count_) frames_[0] = frames_[1]; return true;
  }
  Result takeError() { const Result error = error_; error_ = Result::Status; return error; }
 private:
  CommandFrame frames_[2]; uint8_t count_ = 0; Result error_ = Result::Status;
};
template<class Out> Result apply(Control<Out>& control, const CommandFrame& frame, uint32_t now) {
  const Request request = decodeCommand(frame);
  if (request.error) return Result::Invalid;
  if (request.boot != control.order().boot()) return Result::Old;
  if (request.command != Command::Status && request.sequence <= control.order().sequence()) return Result::Old;
  return control.execute(request, now) ? Result::Refused : Result::Applied;
}
template<class Out> void encodeStatus(uint8_t* out, const Control<Out>& control, Result result, uint32_t reply) {
  const auto& arm = control.arm();
  out[0] = 'B'; out[1] = 'C'; out[2] = 1; out[3] = static_cast<uint8_t>(result);
  write32(out + 4, control.order().boot()); write32(out + 8, control.order().sequence()); write32(out + 12, reply);
  out[16] = (control.servoReady() ? 1 : 0) | (control.watchdogStopped() ? 2 : 0) |
            (static_cast<uint8_t>(arm.state()) << 2) | (!strcmp(arm.sequenceName(), "release") ? 16 : 0);
  out[17] = static_cast<uint8_t>(arm.angle(0)); out[18] = static_cast<uint8_t>(arm.angle(1));
  out[19] = arm.state() == scooper::State::Sequence ? arm.stepIndex() + 1 : 0;
}
}  // namespace companion
