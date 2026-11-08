#pragma once
#include <stdint.h>
#include <stddef.h>
#include <cstdlib>

namespace onebox {
enum class TouchState : uint8_t { NoFrame, Released, Contact, Cancel, Error };
struct TouchFrame {
  TouchState state;
  int16_t x, y;
  uint8_t id;
};
enum class GestureKind : uint8_t { None, Tap, Left, Right };
struct Gesture {
  GestureKind kind;
  int16_t x, y;
};

// Rotation 3 for the DFRobot IPS panel. Reject, never clamp bad coordinates.
inline bool mapTouch(uint16_t x, uint16_t y, bool focalTech, TouchFrame& out) {
  if (x >= 320 || y >= 480) return false;
  out.x = focalTech ? y : 479 - y;
  out.y = focalTech ? x : 319 - x;
  return true;
}

// Bus contract: exact-length reads and acknowledged writes; bounded by the
// platform adapter. No Arduino dependencies, heap allocation or scan delays.
// Sources and controller identification limits: docs/results/esp32-touch-ui-20261020.md.
template <class Bus>
class TouchController {
 public:
  explicit TouchController(Bus& bus) : bus_(bus) {}
  bool begin() {
    kind_ = 0;
    uint8_t id[4] = {}, size[4] = {};
    if (bus_.read(0x5D, 0x8140, 2, id, 4) && id[0] == '9' && id[1] == '1' && id[2] == '1' &&
        id[3] == 0 && bus_.read(0x5D, 0x8048, 2, size, 4) && (size[0] | (size[1] << 8)) == 320 &&
        (size[2] | (size[3] << 8)) == 480) {
      kind_ = 1;
    } else if (bus_.read(0x38, 0xA3, 1, id, 1) && id[0] == 0x54) {
      kind_ = 2;  // FT5436 family, not an unconditional fallback for failed reads.
    }
    return kind_ != 0;
  }
  const char* name() const { return kind_ == 1 ? "GT911" : kind_ == 2 ? "FT54xx" : "unavailable"; }
  TouchFrame poll() {
    TouchFrame out = {TouchState::Error, 0, 0, 0};
    if (kind_ == 1) {
      uint8_t status = 0, point[8] = {};
      if (!bus_.read(0x5D, 0x814E, 2, &status, 1)) return out;
      if (!(status & 0x80)) {
        out.state = TouchState::NoFrame;
        return out;
      }
      const uint8_t count = status & 15;
      if (count == 1 && !bus_.read(0x5D, 0x814F, 2, point, 8)) return out;
      // Acknowledge only after consuming the coherent ready frame.
      if (!bus_.write(0x5D, 0x814E, 0)) return out;
      out.state = count == 0 ? TouchState::Released : TouchState::Cancel;
      if (count == 1 && !(status & 0x70) && point[0] <= 4 &&
          mapTouch(point[1] | (point[2] << 8), point[3] | (point[4] << 8), false, out)) {
        out.state = TouchState::Contact;
        out.id = point[0];
      } else if (status & 0x70)
        out.state = TouchState::Cancel;
    } else if (kind_ == 2) {
      uint8_t data[7] = {};
      if (!bus_.read(0x38, 0, 1, data, sizeof(data))) return out;
      out.state = data[2] == 0 ? TouchState::Released : TouchState::Cancel;
      const uint8_t event = data[3] >> 6;
      if (data[0] != 0)
        out.state = TouchState::Cancel;
      else if (data[2] == 1 && event <= 2 && (data[5] >> 4) < 15 &&
               mapTouch(((data[3] & 15) << 8) | data[4], ((data[5] & 15) << 8) | data[6], true,
                        out)) {
        out.state = event == 1 ? TouchState::Released : TouchState::Contact;
        out.id = data[5] >> 4;
      }
    }
    return out;
  }

 private:
  Bus& bus_;
  uint8_t kind_ = 0;
};

class TouchGestures {
 public:
  void cancel() {
    active_ = false;
    blocked_ = true;
  }
  Gesture update(TouchFrame frame, uint32_t now) {
    Gesture result = {GestureKind::None, 0, 0};
    if (active_ && now - lastFrame_ > 250) cancel();
    if (frame.state == TouchState::NoFrame) return result;  // Not a finger-up event.
    if (frame.state == TouchState::Error || frame.state == TouchState::Cancel) {
      cancel();
      return result;
    }
    if (frame.state == TouchState::Released) {
      if (active_ && !blocked_) {
        const int dx = lastX_ - startX_, dy = lastY_ - startY_;
        const uint32_t elapsed = now - started_;
        result.x = startX_;
        result.y = startY_;
        if (elapsed <= 1500 && std::abs(dx) >= 70 && maxY_ <= 45 && std::abs(dx) > 2 * std::abs(dy))
          result.kind = dx < 0 ? GestureKind::Left : GestureKind::Right;
        else if (elapsed >= 30 && elapsed <= 600 && maxX_ <= 18 && maxY_ <= 18)
          result.kind = GestureKind::Tap;
      }
      active_ = blocked_ = false;
      return result;
    }
    if (frame.x < 0 || frame.x >= 480 || frame.y < 0 || frame.y >= 320) {
      cancel();
      return result;
    }
    if (blocked_) return result;
    if (!active_) {
      active_ = true;
      started_ = now;
      startX_ = frame.x;
      startY_ = frame.y;
      maxX_ = maxY_ = 0;
      id_ = frame.id;
    }
    if (frame.id != id_) {
      cancel();
      return result;
    }
    lastX_ = frame.x;
    lastY_ = frame.y;
    lastFrame_ = now;
    const int dx = std::abs(lastX_ - startX_), dy = std::abs(lastY_ - startY_);
    if (dx > maxX_) maxX_ = dx;
    if (dy > maxY_) maxY_ = dy;
    return result;
  }

 private:
  bool active_ = false, blocked_ = false;
  int16_t startX_ = 0, startY_ = 0, lastX_ = 0, lastY_ = 0;
  int maxX_ = 0, maxY_ = 0;
  uint8_t id_ = 0;
  uint32_t started_ = 0, lastFrame_ = 0;
};
}  // namespace onebox
