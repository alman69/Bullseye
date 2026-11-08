#pragma once
#include "DisplayProtocol.h"

namespace bullseye {
class Renderer {
 public:
  template <class Screen> void render(Screen& screen, const Model& model, const char* touch, uint32_t now) {
    const uint8_t page = model.displayPage(now);
    const bool layout = !painted_ || page_ != page;
    if (layout) {
      screen.fillScreen(kNavy); screen.setTextWrap(false);
      faceRenderer_.invalidate();
    }
    if (page == kFacePage) {
      faceRenderer_.render(screen, model.displayFace(now), now);
      page_ = page; painted_ = true;
      return;  // Full-screen face: no title, labels, status, buttons or tabs.
    }
    const DisplayText text = present(model, touch, now);
    if (layout) {
      print(screen, 12, 12, 2, "BULLSEYE", kCream);
      screen.fillRect(12, 43, 456, 2, kRed);
      if (page == kDistancePage) print(screen, 16, 65, 1, "ULTRASONIC DISTANCE", kCream);
      else if (page == kLinkPage) {
        const char* labels[] = {"Bluetooth", "Rover", "Source", "Freshness", "Last sequence", "Touch controller", "Protocol errors"};
        for (uint8_t i = 0; i < 7; ++i) print(screen, 16, 63 + i * 29, 1, labels[i], kCream);
      }
    }
    if (layout || strcmp(previous_.lines[0], text.lines[0])) field(screen, 275, 17, 194, 15, 1, text.lines[0], kCream);
    if (page == kDistancePage) {
      if (layout || strcmp(previous_.lines[1], text.lines[1])) field(screen, 16, 103, 450, 65, 6, text.lines[1], kCream);
      if (layout || strcmp(previous_.lines[2], text.lines[2])) field(screen, 16, 192, 450, 24, 2, text.lines[2], model.state(now) == ViewState::Live ? kCream : kRed);
      if (layout || strcmp(previous_.lines[3], text.lines[3])) field(screen, 16, 229, 450, 14, 1, text.lines[3], kCream);
      if (layout || strcmp(previous_.lines[4], text.lines[4]) || strcmp(previous_.lines[5], text.lines[5])) {
        char line[80]; snprintf(line, sizeof(line), "BLE: %.20s | ROVER: %.20s", text.lines[4], text.lines[5]);
        field(screen, 16, 251, 450, 12, 1, line, kCream);
      }
    } else {
      const uint8_t rows[] = {4, 5, 0, 2, 6, 7, 8};
      for (uint8_t row = 0; row < 7; ++row) {
        const uint8_t index = rows[row];
        if (layout || strcmp(previous_.lines[index], text.lines[index])) field(screen, 205, 63 + row * 29, 264, 15, 1, text.lines[index], kCream);
      }
    }
    previous_ = text; page_ = page; painted_ = true;
  }
 private:
  template <class Screen> static void print(Screen& screen, int x, int y, int size, const char* text, uint16_t color) {
    screen.setTextColor(color); screen.setTextSize(size); screen.setCursor(x, y); screen.print(text);
  }
  template <class Screen> static void field(Screen& screen, int x, int y, int width, int height, int size, const char* text, uint16_t color) {
    screen.fillRect(x, y, width, height, kNavy); print(screen, x, y, size, text, color);
  }
  DisplayText previous_;
  FaceRenderer faceRenderer_;
  bool painted_ = false;
  uint8_t page_ = 0;
};
}  // namespace bullseye
