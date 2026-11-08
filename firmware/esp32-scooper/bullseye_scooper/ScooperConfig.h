#pragma once
// Everything you are expected to edit lives here: pins, limits, trims and poses.
//
// Angles are "logical" degrees, the same for both sticks:
//   90 = stick pointing straight down, lower = swung outward, higher = swung inward.
// The right servo is mounted mirror-image, so `inverted` flips its angles on the way
// out (180 - angle). That makes one pose move both sticks out (or in) together.
#include <stdint.h>

namespace scooper {

constexpr uint8_t kJointCount = 2;

struct Joint {
  const char* name;  // Shown in `status` and parsed by Mission Control.
  uint8_t pin;       // ESP32-S3 GPIO number.
  uint8_t minDeg;    // Soft limits (logical degrees): commands outside are rejected.
  uint8_t maxDeg;
  uint8_t homeDeg;   // Pose at power-up and on `home`: straight down.
  int8_t trimDeg;    // Added at the servo to correct the horn's spline offset.
  bool inverted;     // Mirror-mounted servo.
};

// Squeeze moves directly to 60 logical degrees and holds until explicit release.
constexpr uint8_t kDownDeg = 90;
constexpr uint8_t kOutDeg = kDownDeg - 30;
constexpr uint8_t kInDeg = kDownDeg + 80;  // Legacy manual angle limit, not a squeeze step.
constexpr uint8_t kSqueezeDeg = 60;
constexpr uint8_t kReleaseDeg = 140;

// SG90 signals: GPIO39 (left) and GPIO40 (right); moved from 41/42 for isolation testing.
// Not 35/36/37 on that header: the board's octal PSRAM uses them. Also avoid 0/45/46
// (boot strapping), 19/20 (USB), 43/44 (debug UART) and 4-13 (display + touch).
// GPIO47/48 use 1.8V logic on the N32R16V module; do not use for these servo signals.
constexpr Joint kJoints[kJointCount] = {
    {"left", 39, kOutDeg - 10, kInDeg + 5, kDownDeg, 0, false},
    {"right", 40, kOutDeg - 10, kInDeg + 5, kDownDeg, 0, true},
};

// SG90: 50 Hz, about 500 us = 0° to 2400 us = 180° (the rover's pan servo uses the same).
constexpr uint16_t kPulseMinUs = 500;
constexpr uint16_t kPulseMaxUs = 2400;
constexpr uint16_t kServoHz = 50;

constexpr uint16_t kDefaultDegPerSec = 90;

// A sequence is a short list of steps. Each step moves both sticks together at
// degPerSec (they start level and travel equally, so they arrive together), then
// waits holdMs. kKeep leaves a joint where it is.
constexpr int16_t kKeep = -1;
struct Step {
  const char* label;
  int16_t deg[kJointCount];
  uint16_t degPerSec;
  uint16_t holdMs;
};

constexpr Step kSqueezeSteps[] = {
    {"squeeze", {kSqueezeDeg, kSqueezeDeg}, 60, 0},
};
constexpr Step kReleaseSteps[] = {
    {"release", {kReleaseDeg, kReleaseDeg}, 90, 0},
};

}  // namespace scooper
