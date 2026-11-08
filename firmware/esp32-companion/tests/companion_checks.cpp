#include <assert.h>
#include <string.h>
#include <string>
#include <iostream>
#include "CompanionHttp.h"
#include "CompanionControl.h"
#include "DisplayJson.h"
#include "DisplayRenderer.h"

using namespace companion;

Request decode(const std::string& text, size_t chunk = 1) {
  HttpDecoder decoder; decoder.reset(100);
  for (size_t offset = 0; offset < text.size(); offset += chunk) {
    decoder.feed(reinterpret_cast<const uint8_t*>(text.data() + offset), std::min(chunk, text.size() - offset));
  }
  assert(decoder.ready()); return decoder.decode();
}
void httpChecks() {
  const std::string status = "GET /status HTTP/1.1\r\nHost: 192.168.4.50\r\n\r\n";
  const std::string command = "POST /command?name=squeeze&boot=42&seq=1 HTTP/1.1\r\nHost: 192.168.4.50\r\nContent-Length: 0\r\n\r\n";
  assert(decode(status).command == Command::Status && decode(status).error == 0);
  for (size_t size : {1, 3, 7, 16, 256}) {
    const auto request = decode(command, size);
    assert(request.error == 0 && request.command == Command::Squeeze && request.boot == 42 && request.sequence == 1);
  }
  for (const std::string& name : {"squeeze", "release", "home", "stop", "relax"}) {
    assert(decode("POST /command?seq=12&boot=42&name=" + name + " HTTP/1.1\r\nContent-Length: 000\r\n\r\n").error == 0);
  }
  assert(decode("GET /command?name=squeeze&boot=42&seq=1 HTTP/1.1\r\n\r\n").error == 405);
  assert(decode("POST /command?name=squeeze HTTP/1.1\r\n\r\n").error == 409);
  assert(decode("POST /command?name=squeeze&boot=42&seq=0 HTTP/1.1\r\n\r\n").error == 409);
  assert(decode("POST /command?name=squeeze&boot=42&seq=4294967296 HTTP/1.1\r\n\r\n").error == 400);
  assert(decode("POST /command?name=squeeze&boot=42&seq=1&seq=2 HTTP/1.1\r\n\r\n").error == 400);
  assert(decode("POST /command?name=invalid&boot=42&seq=1 HTTP/1.1\r\n\r\n").error == 400);
  assert(decode("GET /status HTTP/1.1\r\nContent-Length: 1\r\n\r\nX").error == 413);
  assert(decode("GET /status HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n").error == 400);
  assert(decode("GET /status HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n").error == 400);
  assert(decode("GET /not-found HTTP/1.1\r\n\r\n").error == 404);
  assert(decode("GET /status HTTP/2.0\r\n\r\n").error == 400);
  assert(decode(std::string(1536, 'x')).error == 431);
  assert(decode(std::string("GET /status\0", 12)).error == 400);
  HttpDecoder partial; partial.reset(UINT32_MAX - 100);
  partial.feed(reinterpret_cast<const uint8_t*>("POST /"), 6);
  assert(!partial.ready() && !partial.expired(398) && partial.expired(399));
}

struct Servos {
  bool attached[2] = {}; float angle[2] = {};
  void attach(uint8_t joint) { assert(joint < 2); attached[joint] = true; }
  void detach(uint8_t joint) { assert(joint < 2); attached[joint] = false; }
  void write(uint8_t joint, float value) { assert(joint < 2 && attached[joint] && value >= 0 && value <= 180); angle[joint] = value; }
};
Request request(Command command, uint32_t sequence, uint32_t boot = 42) { return {command, boot, sequence, 0}; }
void orderAndTimeoutChecks() {
  Servos output; Control<Servos> control(output);
  assert(control.begin(0, 42)); control.tick(0, true);
  assert(control.execute(request(Command::Squeeze, 1), 10) == nullptr);
  control.tick(200, true);
  assert(control.execute(request(Command::Stop, 3), 210) == nullptr);
  const auto angle = control.arm().angle(0);
  assert(control.execute(request(Command::Squeeze, 2), 220) != nullptr);
  assert(control.execute(request(Command::Squeeze, 3), 230) != nullptr);
  assert(control.execute(request(Command::Squeeze, 4, 100), 240) != nullptr);
  control.tick(1000, true); assert(control.arm().angle(0) == angle);
  assert(control.execute(request(Command::Squeeze, 4), 1010) == nullptr);
  control.tick(1500, true); control.tick(3010, true);
  assert(control.watchdogStopped() && control.arm().state() == scooper::State::Holding);
  assert(control.order().sequence() == 5 && control.execute(request(Command::Home, 5), 3020));
  assert(control.execute(request(Command::Home, 6), 3100) == nullptr);
  control.tick(3110, false); assert(control.watchdogStopped() && control.order().sequence() == 7);
  control.outputReady(false);
  assert(control.arm().state() == scooper::State::Relaxed);
  assert(control.execute(request(Command::Squeeze, 8), 3120));
  assert(control.execute(request(Command::Stop, 9), 3130) == nullptr);
  // Local USB calibration does not require a remote heartbeat.
  Servos localOutput; Control<Servos> local(localOutput); local.begin(0, 1);
  assert(local.local(Command::Squeeze, 0) == nullptr);
  for (uint32_t now = 10; now < 6000; now += 10) local.tick(now, false);
  assert(local.arm().state() == scooper::State::Holding && local.arm().angle(0) == scooper::kInDeg);
  CommandOrder wrapped; wrapped.begin(9); assert(wrapped.accept(9, UINT32_MAX)); wrapped.fence();
  assert(wrapped.sequence() == UINT32_MAX && !wrapped.accept(9, 1));
}
struct Screen {
  int writes = 0;
  void fillScreen(uint16_t) { ++writes; }
  void setTextWrap(bool) {}
  void setTextColor(uint16_t) {}
  void setTextSize(int) {}
  void setCursor(int, int) {}
  void print(const char*) { assert(false && "Default face is full-screen and has no text"); }
  void fillRect(int x, int y, int w, int h, uint16_t) { assert(x >= 0 && y >= 0 && x + w <= 480 && y + h <= 320); ++writes; }
};
void sharedLoopChecks() {
  Servos output; Control<Servos> control(output); control.begin(0, 42);
  Servos lostOutput; Control<Servos> lostHttp(lostOutput); lostHttp.begin(0, 7);
  bullseye::Model telemetry; telemetry.link(true, 1, 0);
  bullseye::Renderer face; Screen screen;
  HttpDecoder slow; slow.reset(0); slow.feed(reinterpret_cast<const uint8_t*>("POST /"), 6);
  uint32_t sequence = 0; int acks = 0; const int firstWrites = screen.writes;
  assert(control.execute(request(Command::Squeeze, 1), 0) == nullptr);
  assert(lostHttp.execute(request(Command::Squeeze, 1, 7), 0) == nullptr);
  for (uint32_t now = 0; now <= 5000; now += 10) {
    // An incomplete network peer never blocks the main loop.
    if (slow.expired(now)) slow.reset(now);
    if (now % 500 == 0) {
      assert(control.execute(request(Command::Status, 0), now) == nullptr);
      char json[200]; snprintf(json, sizeof(json), "{\"v\":1,\"seq\":%lu,\"distance_cm\":31,\"status\":\"live\",\"sample_age_ms\":0,\"rover_connected\":true,\"source\":\"rover\"}\n", static_cast<unsigned long>(++sequence));
      bullseye::Receiver receiver;
      for (size_t offset = 0; offset < strlen(json); offset += 13) receiver.feed(reinterpret_cast<const uint8_t*>(json + offset), std::min<size_t>(13, strlen(json) - offset), now, 1);
      bullseye::Frame frame; bullseye::Sample sample;
      assert(receiver.pop(frame) && bullseye::decodeSample(frame, sample));
      assert(telemetry.apply(sample, frame, now) == bullseye::Ack::Applied); ++acks;
    }
    control.tick(now, true);
    lostHttp.tick(now, true);  // BLE remains fresh, but this controller gets no HTTP contact.
    if (now % 100 == 0) face.render(screen, telemetry, "GT911", now);
  }
  assert(acks == 11 && telemetry.state(5000) == bullseye::ViewState::Live);
  assert(screen.writes > firstWrites + 100);
  assert(control.arm().state() == scooper::State::Holding && control.arm().angle(0) == scooper::kInDeg);
  assert(lostHttp.watchdogStopped() && lostHttp.arm().angle(0) < scooper::kInDeg);
  assert(telemetry.face.mood(5000) == bullseye::Mood::Curious);
}
int main() {
  httpChecks(); orderAndTimeoutChecks(); sharedLoopChecks();
  std::cout << "Combined companion checks passed: bounded HTTP, ordered STOP, watchdog, BLE framing, face animation and servo updates.\n";
}
