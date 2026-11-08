#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <limits.h>
#include "PixelFace.h"

namespace bullseye {
constexpr char kServiceUuid[] = "3f7c1000-6f54-4a9c-8b2d-56e39a18c201";
constexpr char kRxUuid[] = "3f7c1001-6f54-4a9c-8b2d-56e39a18c201";
constexpr char kAckUuid[] = "3f7c1002-6f54-4a9c-8b2d-56e39a18c201";
constexpr size_t kMaxFrame = 256;  // Includes newline; storage ends with NUL instead.
constexpr uint32_t kPartialTimeoutMs = 2000, kSampleStaleMs = 1500, kDataTimeoutMs = 3000;
enum class Ack : uint8_t { Applied = 0, Invalid = 1, Overflow = 2, Old = 3 };
enum class Status : uint8_t { Live, Stale, Invalid, Unavailable };
enum class Expression : uint8_t { Auto, Happy };
enum class Source : uint8_t { Rover, Demo };

inline void encodeAck(uint8_t out[8], Ack status, uint32_t seq) {
  out[0] = 'B'; out[1] = 'S'; out[2] = 1; out[3] = static_cast<uint8_t>(status);
  for (uint8_t i = 0; i < 4; ++i) out[4 + i] = static_cast<uint8_t>(seq >> (8 * i));
}

struct Frame {
  char text[kMaxFrame] = {};
  uint16_t length = 0;
  uint32_t epoch = 0, startedMs = 0;
};

// External synchronization is provided by the Arduino adapter. No heap allocation
// or JSON parsing occurs here; the BLE callback only frames and queues bytes.
class Receiver {
 public:
  void reset() { length_ = count_ = head_ = 0; dropping_ = false; error_ = 0; }
  void expire(uint32_t now) {
    if (length_ && static_cast<uint32_t>(now - started_) >= kPartialTimeoutMs) {
      length_ = 0; dropping_ = true; error_ = static_cast<uint8_t>(Ack::Invalid);
    }
  }
  void feed(const uint8_t* bytes, size_t size, uint32_t now, uint32_t epoch) {
    expire(now);
    for (size_t i = 0; i < size; ++i) {
      const uint8_t c = bytes[i];
      if (dropping_) { if (c == '\n') dropping_ = false; continue; }
      if (c == '\n') {
        if (length_ == 0) { error_ = static_cast<uint8_t>(Ack::Invalid); continue; }
        if (count_ == 2) error_ = static_cast<uint8_t>(Ack::Overflow);
        else {
          Frame& frame = queue_[(head_ + count_) % 2];
          memcpy(frame.text, buffer_, length_);
          frame.text[length_] = '\0';
          frame.length = length_; frame.epoch = epoch; frame.startedMs = started_;
          ++count_;
        }
        length_ = 0;
      } else if (length_ == kMaxFrame - 1) {
        length_ = 0; dropping_ = true; error_ = static_cast<uint8_t>(Ack::Overflow);
      } else {
        if (length_ == 0) started_ = now;
        buffer_[length_++] = static_cast<char>(c);
      }
    }
  }
  bool pop(Frame& frame) {
    if (!count_) return false;
    frame = queue_[head_]; head_ = (head_ + 1) % 2; --count_; return true;
  }
  uint8_t takeError() { const uint8_t error = error_; error_ = 0; return error; }
  void reject() { error_ = static_cast<uint8_t>(Ack::Invalid); }
 private:
  Frame queue_[2];
  char buffer_[kMaxFrame] = {};
  uint16_t length_ = 0;
  uint8_t count_ = 0, head_ = 0, error_ = 0;
  bool dropping_ = false;
  uint32_t started_ = 0;
};

struct Sample {
  uint32_t seq = 0, ageMs = 0;
  uint16_t cm = 0;
  bool hasDistance = false, hasAge = false, roverConnected = false;
  Status status = Status::Unavailable;
  Source source = Source::Rover;
  Expression expression = Expression::Auto;
};

enum class ViewState : uint8_t { Disconnected, Waiting, Timeout, Unavailable, Invalid, Stale, Live };
class Model {
 public:
  void link(bool connected, uint32_t epoch, uint32_t now = 0) {
    if (connected_ != connected || epoch_ != epoch) {
      connected_ = connected; epoch_ = epoch; hasSample_ = false; sample_ = Sample();
      linked_ = now;
    }
  }
  Ack apply(const Sample& sample, const Frame& frame, uint32_t now) {
    if (!sample.seq) return Ack::Invalid;
    if (!connected_ || frame.epoch != epoch_) return Ack::Old;
    if (hasSample_ && sample.seq <= sample_.seq) return Ack::Old;
    if (sample.expression == Expression::Happy && !happyActive(now)) happyAt_ = now;
    sample_ = sample; hasSample_ = true; started_ = frame.startedMs; applied_ = now;
    return Ack::Applied;
  }
  uint32_t age(uint32_t now) const {
    const uint64_t value = static_cast<uint64_t>(sample_.ageMs) + static_cast<uint32_t>(now - started_);
    return value > UINT32_MAX ? UINT32_MAX : static_cast<uint32_t>(value);
  }
  ViewState state(uint32_t now) const {
    if (!connected_) return ViewState::Disconnected;
    if (!hasSample_) return static_cast<uint32_t>(now - linked_) >= kDataTimeoutMs ? ViewState::Timeout : ViewState::Waiting;
    if (static_cast<uint32_t>(now - applied_) >= kDataTimeoutMs) return ViewState::Timeout;
    if (sample_.status == Status::Invalid) return ViewState::Invalid;
    if (sample_.status == Status::Unavailable || (sample_.source == Source::Rover && !sample_.roverConnected)) return ViewState::Unavailable;
    if (sample_.status == Status::Stale || !sample_.hasAge || age(now) >= kSampleStaleMs) return ViewState::Stale;
    return sample_.hasDistance ? ViewState::Live : ViewState::Unavailable;
  }
  bool happyActive(uint32_t now) const {
    return connected_ && hasSample_ && sample_.roverConnected && sample_.source == Source::Rover &&
           sample_.expression == Expression::Happy && static_cast<uint32_t>(now - applied_) < kDataTimeoutMs;
  }
  uint8_t displayPage(uint32_t now) const { return happyActive(now) ? kFacePage : page; }
  FaceController displayFace(uint32_t now) const {
    FaceController result = face;
    if (happyActive(now)) result.select(Mood::Happy, happyAt_);
    return result;
  }
  const Sample& sample() const { return sample_; }
  bool connected() const { return connected_; }
  bool hasSample() const { return hasSample_; }
  uint32_t errors = 0;
  uint8_t page = kFacePage;
  FaceController face;
 private:
  Sample sample_;
  uint32_t epoch_ = 0, started_ = 0, applied_ = 0, linked_ = 0, happyAt_ = 0;
  bool connected_ = false, hasSample_ = false;
};

inline const char* stateLabel(ViewState state) {
  switch (state) {
    case ViewState::Disconnected: return "BLE disconnected";
    case ViewState::Waiting: return "Waiting for data";
    case ViewState::Timeout: return "Application timed out";
    case ViewState::Unavailable: return "Rover unavailable";
    case ViewState::Invalid: return "No echo / invalid";
    case ViewState::Stale: return "Stale reading";
    default: return "Live distance";
  }
}
struct DisplayText {
  char lines[9][80] = {};
};
inline DisplayText present(const Model& model, const char* touch, uint32_t now) {
  DisplayText text;
  const Sample& sample = model.sample();
  const ViewState state = model.state(now);
  snprintf(text.lines[0], 80, "%s", model.hasSample() && sample.source == Source::Demo ? "DEMO DATA" : "FIELD TELEMETRY");
  if (state == ViewState::Live) snprintf(text.lines[1], 80, "%u%s cm", sample.cm, sample.cm == 150 ? "+" : "");
  else snprintf(text.lines[1], 80, "-- cm");
  snprintf(text.lines[2], 80, "%s", stateLabel(state));
  if (model.hasSample() && sample.hasAge) snprintf(text.lines[3], 80, "Sample age: %lu.%lu s | UNCALIBRATED", static_cast<unsigned long>(model.age(now) / 1000), static_cast<unsigned long>((model.age(now) % 1000) / 100));
  else snprintf(text.lines[3], 80, "Sample age: unknown | UNCALIBRATED");
  snprintf(text.lines[4], 80, "%s", model.connected() ? "Connected" : "Disconnected");
  const bool current = state != ViewState::Disconnected && state != ViewState::Waiting && state != ViewState::Timeout;
  snprintf(text.lines[5], 80, "%s", !current ? "Unknown" : sample.source == Source::Demo ? "Demo (no rover)" : sample.roverConnected ? "Connected" : "Disconnected");
  snprintf(text.lines[6], 80, "%lu", static_cast<unsigned long>(model.hasSample() ? sample.seq : 0));
  snprintf(text.lines[7], 80, "%s", touch);
  snprintf(text.lines[8], 80, "%lu", static_cast<unsigned long>(model.errors));
  return text;
}
}  // namespace bullseye
