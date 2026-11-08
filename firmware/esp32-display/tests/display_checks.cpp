#include <assert.h>
#include <string>
#include <vector>
#include <iostream>
#include <fstream>
#include <algorithm>
#include "DisplayJson.h"
#include "DisplayRenderer.h"
#include "TouchInput.h"
#include "DisplayInput.h"

using namespace bullseye;
const std::string good = "{\"v\":1,\"seq\":42,\"distance_cm\":31,\"status\":\"live\",\"sample_age_ms\":180,\"rover_connected\":true,\"source\":\"rover\"}";

Frame frameOf(const std::string& text, uint32_t epoch = 7) {
  Frame frame; assert(text.size() < kMaxFrame);
  memcpy(frame.text, text.data(), text.size()); frame.length = text.size(); frame.epoch = epoch; frame.startedMs = 100;
  return frame;
}
std::string replaced(std::string text, const std::string& from, const std::string& to) {
  const auto index = text.find(from); assert(index != std::string::npos); text.replace(index, from.size(), to); return text;
}
void framing() {
  Receiver receiver;
  const std::string wire = good + "\n";
  for (size_t i = 0; i < wire.size(); ++i) receiver.feed(reinterpret_cast<const uint8_t*>(&wire[i]), 1, 100 + i, 7);
  Frame frame; assert(receiver.pop(frame)); assert(frame.length == good.size() && frame.epoch == 7 && frame.startedMs == 100);
  assert(std::string(frame.text) == good && !receiver.pop(frame));
  const auto feed = [&receiver](const std::string& data, uint32_t now = 1000) { receiver.feed(reinterpret_cast<const uint8_t*>(data.data()), data.size(), now, 7); };
  feed(wire + wire + wire); assert(receiver.takeError() == static_cast<uint8_t>(Ack::Overflow));
  assert(receiver.pop(frame) && receiver.pop(frame) && !receiver.pop(frame));
  feed(std::string(255, 'x') + "\n"); assert(receiver.pop(frame) && frame.length == 255);
  feed(std::string(256, 'x') + "\n" + wire); assert(receiver.takeError() == static_cast<uint8_t>(Ack::Overflow));
  assert(receiver.pop(frame) && std::string(frame.text) == good);
  feed(good.substr(0, 20), 1000); receiver.expire(3000);
  assert(receiver.takeError() == static_cast<uint8_t>(Ack::Invalid));
  feed(good.substr(20) + "\n" + wire, 3001);
  assert(receiver.pop(frame) && std::string(frame.text) == good && frame.startedMs == 3001);
  feed(wire); receiver.reset(); assert(!receiver.pop(frame));
  feed("{", UINT32_MAX - 10); receiver.expire(10); assert(receiver.takeError() == 0);
  receiver.expire(2100); assert(receiver.takeError() == static_cast<uint8_t>(Ack::Invalid));
}
void schemas() {
  Sample sample;
  assert(decodeSample(frameOf(good), sample) && sample.cm == 31 && sample.seq == 42);
  assert(decodeSample(frameOf(" " + good + " "), sample));
  const std::vector<std::string> invalid = {
    "{}", "[]", good + "junk", "{bad}",
    replaced(good, "\"v\":1", "\"v\":2"),
    replaced(good, "\"seq\":42", "\"seq\":0"),
    replaced(good, "\"seq\":42", "\"seq\":4294967296"),
    replaced(good, "\"seq\":42", "\"seq\":true"),
    replaced(good, "\"seq\":42", "\"seq\":42,\"seq\":43"),
    replaced(good, "\"distance_cm\":31", "\"distance_cm\":0"),
    replaced(good, "\"distance_cm\":31", "\"distance_cm\":151"),
    replaced(good, "\"distance_cm\":31", "\"distance_cm\":31.5"),
    replaced(good, "\"distance_cm\":31", "\"distance_cm\":true"),
    replaced(good, "\"distance_cm\":31", "\"distance_cm\":null"),
    replaced(good, "\"sample_age_ms\":180", "\"sample_age_ms\":-1"),
    replaced(good, "\"rover_connected\":true", "\"rover_connected\":\"true\""),
    replaced(good, "\"live\"", "\"invalid\""),
    replaced(good, "\"live\"", "\"live\\u0000old\""),
    replaced(good, "\"rover\"", "\"other\""),
    replaced(good, "\"v\":1", "\"unexpected\":1,\"v\":1"),
  };
  for (const auto& text : invalid) { Sample parsed; assert(!decodeSample(frameOf(text), parsed)); }
  auto nul = good; nul.insert(10, 1, '\0'); assert(!decodeSample(frameOf(nul), sample));
  auto unknown = replaced(replaced(good, "\"distance_cm\":31", "\"distance_cm\":null"), "\"live\"", "\"invalid\"");
  assert(decodeSample(frameOf(unknown), sample) && !sample.hasDistance && sample.status == Status::Invalid);
}
void states() {
  Model model; Frame frame = frameOf(good); Sample sample; assert(decodeSample(frame, sample));
  assert(model.state(100) == ViewState::Disconnected);
  model.link(true, 7, 100); assert(model.state(100) == ViewState::Waiting);
  assert(model.state(3099) == ViewState::Waiting && model.state(3100) == ViewState::Timeout);
  assert(model.apply(sample, frame, 200) == Ack::Applied);
  assert(model.age(300) == 380 && model.state(1419) == ViewState::Live);
  assert(model.state(1420) == ViewState::Stale);
  assert(model.apply(sample, frame, 3000) == Ack::Old);
  assert(model.state(3200) == ViewState::Timeout);  // Duplicate did not refresh liveness.
  model.link(false, 8); assert(!model.hasSample() && model.state(3201) == ViewState::Disconnected);
  model.link(true, 9, 3202); assert(model.apply(sample, frame, 3202) == Ack::Old && !model.hasSample());
  assert(model.state(3202) == ViewState::Waiting);
  frame.epoch = 9; frame.startedMs = 3300; sample.seq = 1;
  assert(model.apply(sample, frame, 3300) == Ack::Applied);
  sample.seq = 2; sample.status = Status::Invalid; sample.hasDistance = false;
  assert(model.apply(sample, frame, 3300) == Ack::Applied && model.state(3300) == ViewState::Invalid);
  sample.seq = 3; sample.status = Status::Unavailable;
  model.apply(sample, frame, 3300); assert(model.state(3300) == ViewState::Unavailable);
  sample.seq = 4; sample.status = Status::Live; sample.hasDistance = true; sample.roverConnected = false;
  model.apply(sample, frame, 3300); assert(model.state(3300) == ViewState::Unavailable);
  sample.seq = 5; sample.source = Source::Demo; sample.ageMs = 0;
  model.apply(sample, frame, 3300); assert(model.state(3300) == ViewState::Live);
  sample.seq = 6; sample.ageMs = UINT32_MAX;
  model.apply(sample, frame, 3300); assert(model.age(3400) == UINT32_MAX);
  uint8_t ack[8]; encodeAck(ack, Ack::Applied, 42);
  const uint8_t expected[] = {'B', 'S', 1, 0, 42, 0, 0, 0}; assert(!memcmp(ack, expected, 8));
}
struct Canvas {
  int x = 0, y = 0, size = 1, calls = 0, layouts = 0;
  size_t written = 0;
  std::vector<uint16_t> pixels = std::vector<uint16_t>(480 * 320, kNavy);
  std::vector<std::string> texts;
  void fillScreen(uint16_t color) { ++calls; ++layouts; std::fill(pixels.begin(), pixels.end(), color); }
  void setTextWrap(bool) {}
  void setTextColor(uint16_t) {}
  void setTextSize(int value) { size = value; }
  void setCursor(int a, int b) { x = a; y = b; }
  void print(const char* text) {
    assert(x >= 0 && y >= 0 && x + static_cast<int>(strlen(text)) * 6 * size <= 480 && y + 8 * size <= 320); ++calls;
    texts.emplace_back(text);
  }
  void fillRect(int a, int b, int w, int h, uint16_t color) {
    assert(a >= 0 && b >= 0 && w > 0 && h > 0 && a + w <= 480 && b + h <= 320); ++calls;
    written += w * h;
    for (int row = b; row < b + h; ++row) for (int col = a; col < a + w; ++col) pixels[row * 480 + col] = color;
  }
};
void rendering() {
  Model model; model.page = kDistancePage; Canvas canvas; Renderer renderer;
  renderer.render(canvas, model, "unavailable", 0);
  const int before = canvas.calls; renderer.render(canvas, model, "unavailable", 0); assert(before == canvas.calls);
  model.link(true, 7); Frame frame = frameOf(good); Sample sample; decodeSample(frame, sample); model.apply(sample, frame, 200);
  renderer.render(canvas, model, "GT911", 200);
  assert(std::string(present(model, "GT911", 200).lines[1]) == "31 cm");
  sample.seq++; sample.cm = 150; model.apply(sample, frame, 300); renderer.render(canvas, model, "FT54xx", 300);
  assert(std::string(present(model, "FT54xx", 300).lines[1]) == "150+ cm");
  renderer.render(canvas, model, "FT54xx", 1700); assert(std::string(present(model, "FT54xx", 1700).lines[1]) == "-- cm");
  model.page = 1; renderer.render(canvas, model, "unavailable", 1800); assert(canvas.layouts == 2);
  renderer.render(canvas, model, "unavailable", 3500);
  model.link(false, 8); renderer.render(canvas, model, "unavailable", 3600);
}
void assertFace(const Canvas& canvas, const FaceController& controller, uint32_t now) {
  constexpr uint16_t palette[] = {kNavy, kCream, kSteel, kRed};
  const FaceFrame expected = pixelFace(controller.mood(now), controller.elapsed(now));
  for (int row = 0; row < FaceFrame::rows * FaceFrame::cell; ++row) {
    for (int col = 0; col < FaceFrame::columns * FaceFrame::cell; ++col) {
      const auto ink = expected.cells[(row / FaceFrame::cell) * FaceFrame::columns + col / FaceFrame::cell];
      assert(canvas.pixels[(FaceFrame::y + row) * 480 + FaceFrame::x + col] == palette[ink]);
    }
  }
}
void faces() {
  const uint32_t animatedAt[] = {3600, 400, 600, 2000, 800};
  std::vector<FaceFrame> expressions;
  for (uint8_t value = 0; value < 5; ++value) {
    const auto mood = static_cast<Mood>(value);
    const auto first = pixelFace(mood, 0), animated = pixelFace(mood, animatedAt[value]);
    assert(memcmp(first.cells, animated.cells, sizeof(first.cells)));  // Each expression animates.
    for (const auto& other : expressions) assert(memcmp(first.cells, other.cells, sizeof(first.cells)));
    expressions.push_back(first);
    for (uint32_t time = 0; time < 8100; time += 100) {
      const auto frame = pixelFace(mood, time);
      bool visible = false;
      for (const auto ink : frame.cells) { assert(ink <= 3); visible |= ink == static_cast<uint8_t>(Ink::Cream); }
      assert(visible);
    }
  }
  FaceController controller;
  assert(controller.mood(0) == Mood::Happy && controller.automatic());
  assert(controller.mood(3999) == Mood::Happy && controller.mood(4000) == Mood::Curious);
  assert(controller.mood(8000) == Mood::Grumpy && controller.mood(12000) == Mood::Sleepy);
  assert(controller.mood(16000) == Mood::Idle && controller.mood(20000) == Mood::Happy);
  controller.toggleAuto(8500); assert(controller.mood(100000) == Mood::Grumpy && !controller.automatic());
  controller.next(10000); assert(controller.mood(10000) == Mood::Sleepy);
  FaceController wrapped; wrapped.toggleAuto(0); wrapped.toggleAuto(UINT32_MAX - 1000);
  assert(wrapped.mood(2999) == Mood::Curious && wrapped.elapsed(2999) == 0);

  Model model; Canvas canvas; Renderer renderer;
  assert(model.page == kFacePage);
  renderer.render(canvas, model, "GT911", 0); assertFace(canvas, model.face, 0);
  assert(canvas.texts.empty());  // No title, captions, telemetry, tabs or button text.
  const int calls = canvas.calls; renderer.render(canvas, model, "GT911", 0); assert(canvas.calls == calls);
  const auto written = canvas.written;
  renderer.render(canvas, model, "GT911", 400); assertFace(canvas, model.face, 400);
  assert(canvas.written - written < FaceFrame::columns * FaceFrame::rows * FaceFrame::cell * FaceFrame::cell / 2);
  for (uint32_t now = 500; now <= 900; now += 100) {
    model.face.next(now); renderer.render(canvas, model, "GT911", now); assertFace(canvas, model.face, now);
  }
  model.page = kLinkPage; renderer.render(canvas, model, "GT911", 1000);
  canvas.texts.clear();
  model.page = kFacePage; renderer.render(canvas, model, "GT911", 1000); assertFace(canvas, model.face, 1000);
  assert(canvas.texts.empty());
  // Face navigation/animation never refreshes or manufactures telemetry.
  model.link(true, 7, 1000); auto frame = frameOf(good); Sample sample; decodeSample(frame, sample);
  frame.startedMs = 1000; model.apply(sample, frame, 1000);
  renderer.render(canvas, model, "GT911", 1000);
  renderer.render(canvas, model, "GT911", 2400);
  assert(model.state(2400) == ViewState::Stale);
  assert(canvas.texts.empty());
  renderer.render(canvas, model, "GT911", 4000); assert(model.state(4000) == ViewState::Timeout);
  model.link(false, 8, 4100); renderer.render(canvas, model, "unavailable", 4100);
  assert(!model.hasSample());
  assert(canvas.texts.empty());

  // Optional host-only artwork export; no serial/BLE/hardware access.
  if (const char* directory = std::getenv("BULLSEYE_FACE_PREVIEW_DIR")) {
    for (uint8_t value = 0; value < 5; ++value) {
      Canvas image; FaceRenderer face; FaceController expression;
      while (expression.mood(0) != static_cast<Mood>(value)) expression.next(0);
      face.render(image, expression, 0);
      std::ofstream out(std::string(directory) + "/" + moodName(static_cast<Mood>(value)) + ".ppm", std::ios::binary);
      assert(out); out << "P6\n480 320\n255\n";
      for (const auto color : image.pixels) {
        const char rgb[] = {static_cast<char>(((color >> 11) & 31) * 255 / 31),
                            static_cast<char>(((color >> 5) & 63) * 255 / 63), static_cast<char>((color & 31) * 255 / 31)};
        out.write(rgb, 3);
      }
    }
  }
}
void navigation() {
  Model model;
  using onebox::GestureKind;
  const auto gesture = [&model](GestureKind kind, int x = 0, int y = 0, uint32_t now = 0) {
    handleGesture(model, {kind, static_cast<int16_t>(x), static_cast<int16_t>(y)}, now);
  };
  gesture(GestureKind::Left); assert(model.page == kDistancePage);
  gesture(GestureKind::Left); assert(model.page == kLinkPage);
  gesture(GestureKind::Left); assert(model.page == kFacePage);
  gesture(GestureKind::Right); assert(model.page == kLinkPage);
  gesture(GestureKind::Right); gesture(GestureKind::Right); assert(model.page == kFacePage);
  for (const int x : {0, 159, 160, 319, 320, 479}) {
    gesture(GestureKind::Tap, x, 274); assert(model.page == kFacePage);
  }
  gesture(GestureKind::Tap, -1, 290); gesture(GestureKind::Tap, 480, 290); gesture(GestureKind::Tap, 200, 320);
  assert(model.page == kFacePage);
  gesture(GestureKind::Tap, 408, 250, 100); assert(model.face.automatic());
  gesture(GestureKind::Tap, 240, 120, 200); assert(model.face.automatic() && model.face.mood(200) == Mood::Happy);
  gesture(GestureKind::Tap, 120, 250, 200); assert(model.face.mood(200) == Mood::Happy);
  model.page = kDistancePage; gesture(GestureKind::Tap, 408, 250, 300); assert(model.face.automatic());
}
void arrivalExpression() {
  const std::string happy = replaced(good, "\"v\":1", "\"expression\":\"happy\",\"v\":1");
  Sample sample;
  assert(decodeSample(frameOf(happy), sample) && sample.expression == Expression::Happy);
  assert(decodeSample(frameOf(good), sample) && sample.expression == Expression::Auto);
  assert(decodeSample(frameOf(replaced(happy, "\"happy\"", "\"auto\"")), sample));
  for (const auto& value : {"null", "true", "1", "[]", "\"grumpy\""})
    assert(!decodeSample(frameOf(replaced(happy, "\"happy\"", value)), sample));
  assert(!decodeSample(frameOf(replaced(happy, "\"v\":1", "\"expression\":\"auto\",\"v\":1")), sample));
  Model model; model.page = kDistancePage; model.link(true, 7, 100);
  Frame frame = frameOf(happy); assert(decodeSample(frame, sample));
  assert(model.apply(sample, frame, 200) == Ack::Applied);
  assert(model.happyActive(200) && model.displayPage(200) == kFacePage);
  assert(model.displayFace(200).mood(200) == Mood::Happy && model.face.automatic());
  Canvas canvas; Renderer renderer; renderer.render(canvas, model, "GT911", 200);
  assert(canvas.texts.empty()); assertFace(canvas, model.displayFace(200), 200);
  renderer.render(canvas, model, "GT911", 700); assertFace(canvas, model.displayFace(700), 700);
  sample.seq++; model.apply(sample, frame, 1000);
  assert(model.displayFace(1000).elapsed(1000) == 800);  // repeated packets do not restart animation
  handleGesture(model, {onebox::GestureKind::Left, 200, 100}, 1100);
  assert(model.page == kDistancePage);
  assert(model.happyActive(3999) && !model.happyActive(4000));
  assert(model.displayPage(4000) == kDistancePage && model.displayFace(4000).automatic());
  sample.seq++; sample.expression = Expression::Auto; model.apply(sample, frame, 1200);
  assert(!model.happyActive(1200));
  renderer.render(canvas, model, "GT911", 1200); assert(!canvas.texts.empty());
  sample.seq++; sample.expression = Expression::Happy; model.apply(sample, frame, 1300);
  assert(model.happyActive(1300)); model.link(false, 8, 1400); assert(!model.happyActive(1400));
}
struct Bus {
  bool gt = true;
  bool read(uint8_t address, uint16_t reg, uint8_t, uint8_t* out, size_t count) {
    memset(out, 0, count);
    if (gt && address == 0x5D && reg == 0x8140 && count == 4) { memcpy(out, "911", 3); return true; }
    if (gt && address == 0x5D && reg == 0x8048 && count == 4) { out[0] = 0x40; out[1] = 1; out[2] = 0xe0; out[3] = 1; return true; }
    if (!gt && address == 0x38 && reg == 0xA3 && count == 1) { out[0] = 0x54; return true; }
    return false;
  }
  bool write(uint8_t, uint16_t, uint8_t) { return true; }
};
void touching() {
  onebox::TouchFrame frame;
  assert(onebox::mapTouch(0, 0, false, frame) && frame.x == 479 && frame.y == 319);
  assert(onebox::mapTouch(319, 479, true, frame) && frame.x == 479 && frame.y == 319);
  assert(!onebox::mapTouch(320, 0, false, frame));
  Bus bus; onebox::TouchController<Bus> controller(bus); assert(controller.begin() && !strcmp(controller.name(), "GT911"));
  bus.gt = false; assert(controller.begin() && !strcmp(controller.name(), "FT54xx"));
  onebox::TouchGestures gestures;
  gestures.update({onebox::TouchState::Contact, 100, 295, 1}, 0);
  auto tap = gestures.update({onebox::TouchState::Released, 0, 0, 1}, 100);
  assert(tap.kind == onebox::GestureKind::Tap && tap.y == 295);
  gestures.update({onebox::TouchState::Contact, 300, 100, 1}, 200);
  gestures.update({onebox::TouchState::Contact, 190, 100, 1}, 300);
  assert(gestures.update({onebox::TouchState::Released, 0, 0, 1}, 350).kind == onebox::GestureKind::Left);
}
int main() {
  framing(); schemas(); states(); rendering(); faces(); navigation(); touching(); arrivalExpression();
  std::cout << "Native display checks passed: framing, schemas, ACK, freshness, reconnect, rendering, pixel animations, three-page touch navigation.\n";
}
