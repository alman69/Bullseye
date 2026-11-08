#pragma once
#include <stdint.h>
#include <string.h>

namespace bullseye {
constexpr uint16_t kNavy = 0x0884, kCream = 0xef3a, kSteel = 0x326c, kRed = 0xe1c5;
constexpr uint8_t kDistancePage = 0, kLinkPage = 1, kFacePage = 2, kPageCount = 3;
enum class Mood : uint8_t { Idle, Happy, Curious, Grumpy, Sleepy };
inline const char* moodName(Mood mood) {
  switch (mood) {
    case Mood::Happy: return "HAPPY";
    case Mood::Curious: return "CURIOUS";
    case Mood::Grumpy: return "GRUMPY";
    case Mood::Sleepy: return "SLEEPY";
    default: return "IDLE";
  }
}

// Local animation only: no sensor thresholds, BLE fields or rover commands.
class FaceController {
 public:
  Mood mood(uint32_t now) const {
    const uint32_t steps = automatic_ ? static_cast<uint32_t>(now - changedAt_) / 4000 : 0;
    return static_cast<Mood>((static_cast<uint8_t>(selected_) + steps) % 5);
  }
  uint32_t elapsed(uint32_t now) const {
    const uint32_t age = now - changedAt_;
    return automatic_ ? age % 4000 : age;
  }
  bool automatic() const { return automatic_; }
  void next(uint32_t now) {
    selected_ = static_cast<Mood>((static_cast<uint8_t>(mood(now)) + 1) % 5);
    automatic_ = false; changedAt_ = now;
  }
  void toggleAuto(uint32_t now) {
    selected_ = mood(now); automatic_ = !automatic_; changedAt_ = now;
  }
 private:
  Mood selected_ = Mood::Happy;
  bool automatic_ = true;
  uint32_t changedAt_ = 0;
};

enum class Ink : uint8_t { Navy, Cream, Steel, Red };
// Eight-pixel cells enlarge the approved artwork across the entire LCD. Two tiny buffers replace
// a full-screen framebuffer; only changed horizontal runs reach the SPI display.
struct FaceFrame {
  static constexpr int columns = 60, rows = 40, cell = 8, x = 0, y = 0;
  uint8_t cells[columns * rows] = {};
  void rect(int left, int top, int width, int height, Ink ink = Ink::Cream) {
    left += 5; top += 5;  // Center the original face geometry in 480x320.
    for (int row = top; row < top + height; ++row) {
      if (row < 0 || row >= rows) continue;
      for (int col = left; col < left + width; ++col) {
        if (col >= 0 && col < columns) cells[row * columns + col] = static_cast<uint8_t>(ink);
      }
    }
  }
};

inline FaceFrame pixelFace(Mood mood, uint32_t elapsed) {
  FaceFrame frame;
  const bool blink = elapsed % 3800 >= 3500 && elapsed % 3800 < 3700;
  const int bob = mood == Mood::Happy && elapsed % 800 >= 400 ? 1 : 0;
  const int glance = mood == Mood::Curious ? static_cast<int>((elapsed / 600) % 4) - 1 : 0;
  const int gaze = glance > 1 ? 0 : glance;
  const int twitch = mood == Mood::Grumpy && elapsed % 2200 >= 1900 ? 1 : 0;
  const int breath = mood == Mood::Sleepy && elapsed % 1600 >= 800 ? 1 : 0;
  for (int index = 0; index < 2; ++index) {
    const int center = index == 0 ? 12 : 37;
    if (mood == Mood::Happy) {
      frame.rect(center - 5, 4 + bob, 3, 1);
      frame.rect(center - 2, 3 + bob, 5, 1);
      frame.rect(center + 3, 4 + bob, 3, 1);
      if (blink) frame.rect(center - 4, 13 + bob, 9, 1);
      else {
        frame.rect(center - 5, 11 + bob, 2, 4);
        frame.rect(center - 3, 10 + bob, 2, 2);
        frame.rect(center - 1, 9 + bob, 3, 2);
        frame.rect(center + 2, 10 + bob, 2, 2);
        frame.rect(center + 4, 11 + bob, 2, 4);
      }
    } else if (mood == Mood::Sleepy) {
      frame.rect(center - 4, 8, 9, 1, Ink::Steel);
      frame.rect(center - 5, 13 + breath, 3, 1);
      frame.rect(center - 2, 14 + breath, 5, 1);
      frame.rect(center + 3, 13 + breath, 3, 1);
    } else {
      if (mood == Mood::Grumpy) {
        frame.rect(center - 5, (index == 0 ? 4 : 7) + twitch, 5, 2, Ink::Red);
        frame.rect(center, (index == 0 ? 7 : 4) + twitch, 6, 2, Ink::Red);
        if (blink) frame.rect(center - 4, 13, 9, 1);
        else {
          frame.rect(center - 4, 11, 9, 6);
          for (int row = 0; row < 3; ++row) {
            const int cut = 6 - row * 2;
            frame.rect(center - 4 + (index == 0 ? 0 : cut), 8 + row, 9 - cut, 1);
          }
        }
      } else {
        if (mood == Mood::Curious) {
          const int raise = index == 0 && elapsed % 1600 >= 800 ? 1 : 0;
          frame.rect(center - 5, (index == 0 ? 3 : 6) - raise, 5, 1);
          frame.rect(center, (index == 0 ? 2 : 6) - raise, 6, 1);
        } else frame.rect(center - 4, 4, 9, 1, Ink::Steel);
        if (blink) frame.rect(center - 4 + gaze, 13, 9, 1);
        else frame.rect(center - 4 + gaze, mood == Mood::Curious && index == 1 ? 10 : 7,
                        9, mood == Mood::Curious ? (index == 0 ? 13 : 9) : 11);
      }
    }
  }
  if (mood == Mood::Happy) {
    frame.rect(19, 20 + bob, 2, 4); frame.rect(21, 23 + bob, 2, 2);
    frame.rect(23, 25 + bob, 6, 2); frame.rect(29, 23 + bob, 2, 2); frame.rect(31, 20 + bob, 2, 4);
    frame.rect(3, 17 + bob, 3, 1, Ink::Red); frame.rect(46, 17 + bob, 3, 1, Ink::Red);
  } else if (mood == Mood::Grumpy) {
    frame.rect(21, 25, 2, 2); frame.rect(23, 24, 2, 1); frame.rect(25, 23, 4, 1);
    frame.rect(29, 24, 2, 1); frame.rect(31, 25, 2, 2);
    const int pulse = (elapsed / 300) % 2;
    frame.rect(50, 5 + pulse, 1, 3, Ink::Red); frame.rect(52, 8 + pulse, 3, 1, Ink::Red);
  } else if (mood == Mood::Sleepy) {
    frame.rect(24, 22, 4, 1); frame.rect(24, 25 + breath, 4, 1);
    frame.rect(24, 23, 1, 2 + breath); frame.rect(27, 23, 1, 2 + breath);
    const int drift = (elapsed / 500) % 3;
    frame.rect(46, 3 - drift, 4, 1, Ink::Red); frame.rect(48, 4 - drift, 1, 1, Ink::Red);
    frame.rect(47, 5 - drift, 1, 1, Ink::Red); frame.rect(46, 6 - drift, 4, 1, Ink::Red);
  } else if (mood == Mood::Curious) {
    frame.rect(23, 23, 5, 1); frame.rect(28, 22, 1, 2); frame.rect(29, 22, 3, 1);
  } else {
    frame.rect(22, 22, 1, 1); frame.rect(22, 23, 8, 1); frame.rect(29, 22, 1, 1);
  }
  return frame;
}

class FaceRenderer {
 public:
  void invalidate() { valid_ = false; }
  template <class Screen> void render(Screen& screen, const FaceController& controller, uint32_t now) {
    const FaceFrame next = pixelFace(controller.mood(now), controller.elapsed(now));
    constexpr uint16_t palette[] = {kNavy, kCream, kSteel, kRed};
    for (int row = 0; row < FaceFrame::rows; ++row) {
      int col = 0;
      while (col < FaceFrame::columns) {
        const int index = row * FaceFrame::columns + col;
        const auto ink = next.cells[index];
        if ((valid_ && ink == previous_.cells[index]) || (!valid_ && ink == 0)) { ++col; continue; }
        const int start = col++;
        while (col < FaceFrame::columns && next.cells[row * FaceFrame::columns + col] == ink &&
               (!valid_ || previous_.cells[row * FaceFrame::columns + col] != ink)) ++col;
        screen.fillRect(FaceFrame::x + start * FaceFrame::cell, FaceFrame::y + row * FaceFrame::cell,
                        (col - start) * FaceFrame::cell, FaceFrame::cell, palette[ink]);
      }
    }
    previous_ = next; valid_ = true;
  }
 private:
  FaceFrame previous_;
  bool valid_ = false;
};
}  // namespace bullseye
