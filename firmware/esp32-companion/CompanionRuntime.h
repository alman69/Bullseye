#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <NetworkServer.h>
#include <NetworkClient.h>
#include <esp_random.h>
#include <lwip/sockets.h>
#include <errno.h>
#include "CompanionHttp.h"
#include "CompanionControl.h"
#include "Scooper.h"
#include "ServoOutput.h"
#include "WifiConfig.h"

namespace companion {
class Runtime {
 public:
  Runtime() : control_(servos_), listener_(scooper::kHttpPort) {}
  void begin() {
    bool output = control_.begin(millis(), esp_random());
    for (uint8_t i = 0; i < scooper::kJointCount; ++i) output &= ledcReadFreq(scooper::kJoints[i].pin) > 0;
    control_.outputReady(output);
    if (!output) Serial.println("Scooper PWM failed; motion disabled until restart.");
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
    if (!WiFi.config(ip(scooper::kScooperIp), ip(scooper::kGatewayIp), ip(scooper::kSubnetMask))) {
      Serial.println("Scooper fixed-IP configuration failed; inspect Wi-Fi before use.");
    }
    joinWifi();
    Serial.println("Combined display + scooper ready. Serial: status, wifi, stop, relax, home, squeeze, release, set 1|2 degrees [speed].");
    printStatus();
  }
  void poll(uint32_t now) {
    readSerial(now);
    const bool wifi = WiFi.status() == WL_CONNECTED;
    wifiConnected_ = wifi;
    if (wifi && !listening_) {
      listener_.begin(); listening_ = true;
      Serial.printf("Companion Wi-Fi: http://%s\n", WiFi.localIP().toString().c_str());
    } else if (!wifi && listening_) {
      closeClient(); listener_.end(); listening_ = false;
    }
    if (!wifi && static_cast<uint32_t>(now - lastWifiAttempt_) >= scooper::kWifiRetryMs) joinWifi();
    if (wifi) serve(now);
    control_.tick(millis(), wifi);
  }
  // Called around longer screen operations as well as on every loop iteration.
  void updateMotion(uint32_t now) { control_.tick(now, wifiConnected_); }
 private:
  static IPAddress ip(const uint8_t (&parts)[4]) { return IPAddress(parts[0], parts[1], parts[2], parts[3]); }
  void joinWifi() {
    lastWifiAttempt_ = millis();
    WiFi.disconnect();
    WiFi.begin(scooper::kWifiSsid, scooper::kWifiPassword[0] ? scooper::kWifiPassword : nullptr);
  }
  void statusJson(char* out, size_t capacity) const {
    const auto& arm = control_.arm();
    const bool sequence = arm.state() == scooper::State::Sequence;
    char labels[80];
    if (sequence) snprintf(labels, sizeof(labels), "\"%s\",\"step\":\"%s\"", arm.sequenceName(), arm.stepLabel());
    else snprintf(labels, sizeof(labels), "null,\"step\":null");
    snprintf(out, capacity,
      "{\"firmware\":\"bullseye-companion\",\"api\":2,\"boot_id\":%lu,\"command_seq\":%lu,\"servo_ready\":%s,\"watchdog_stopped\":%s,\"state\":\"%s\",\"sequence\":%s,\"angles\":{\"left\":%d,\"right\":%d}}",
      static_cast<unsigned long>(control_.order().boot()), static_cast<unsigned long>(control_.order().sequence()), control_.servoReady() ? "true" : "false",
      control_.watchdogStopped() ? "true" : "false", scooper::stateName(arm.state()), labels, arm.angle(0), arm.angle(1));
  }
  void respond(int code, const char* error = nullptr, bool commandReply = false) {
    char status[384], body[512]; statusJson(status, sizeof(status));
    if (error) snprintf(body, sizeof(body), "{\"ok\":false,\"error\":\"%s\",\"status\":%s}", error, status);
    else if (commandReply) snprintf(body, sizeof(body), "{\"ok\":true,\"status\":%s}", status);
    else snprintf(body, sizeof(body), "%s", status);
    const char* reason = code == 200 ? "OK" : code == 409 ? "Conflict" : code == 404 ? "Not Found" :
                         code == 405 ? "Method Not Allowed" : code == 413 ? "Payload Too Large" : code == 431 ? "Request Header Fields Too Large" : "Bad Request";
    const int length = snprintf(response_, sizeof(response_),
      "HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: %u\r\n\r\n%s",
      code, reason, static_cast<unsigned>(strlen(body)), body);
    if (length < 0 || static_cast<size_t>(length) >= sizeof(response_)) { closeClient(); return; }
    responseLength_ = static_cast<size_t>(length); sent_ = 0;
  }
  void serve(uint32_t now) {
    if (!active_) {
      client_ = listener_.accept();
      if (!client_) return;
      active_ = true; decoder_.reset(now); responseLength_ = sent_ = 0;
    }
    if (!client_.connected() || decoder_.expired(now)) { closeClient(); return; }
    if (!responseLength_) {
      const int available = client_.available();
      if (available > 0) {
        uint8_t bytes[256];
        const int count = client_.read(bytes, available > 256 ? 256 : available);
        if (count > 0) decoder_.feed(bytes, static_cast<size_t>(count));
      }
      if (!decoder_.ready()) return;
      const Request request = decoder_.decode();
      if (request.error) respond(request.error, request.error == 409 ? "update Mission Control; boot and seq are required" : "unsupported HTTP request");
      else {
        const char* error = control_.execute(request, now);
        respond(error ? 409 : 200, error, request.command != Command::Status);
      }
    }
    if (responseLength_) {
      const size_t remaining = responseLength_ - sent_;
      // NetworkClient::write retries on slow peers. One nonblocking send instead
      // keeps BLE framing, touch, servo slew and face animation running.
      const int count = ::send(client_.fd(), response_ + sent_, remaining > 384 ? 384 : remaining, MSG_DONTWAIT);
      if (count > 0) sent_ += static_cast<size_t>(count);
      else if (count < 0 && errno != EAGAIN && errno != EWOULDBLOCK) { closeClient(); return; }
      if (sent_ == responseLength_) closeClient();
    }
  }
  void closeClient() { client_.stop(); active_ = false; responseLength_ = sent_ = 0; }
  void printStatus() const { char status[384]; statusJson(status, sizeof(status)); Serial.println(status); }
  void serialCommand() {
    line_[lineLength_] = '\0';
    char* name = strtok(line_, " \t"); if (!name) return;
    if (!strcmp(name, "status")) { printStatus(); return; }
    if (!strcmp(name, "wifi")) { Serial.printf("Wi-Fi: %s / %s\n", WiFi.status() == WL_CONNECTED ? "connected" : "disconnected", WiFi.localIP().toString().c_str()); return; }
    if (!strcmp(name, "x")) name = const_cast<char*>("stop");
    if (!strcmp(name, "set")) {
      uint32_t joint = 0, degrees = 0, speed = scooper::kDefaultDegPerSec;
      const char* speedText;
      if (!unsigned32(strtok(nullptr, " \t"), joint) || !unsigned32(strtok(nullptr, " \t"), degrees) ||
          ((speedText = strtok(nullptr, " \t")) && !unsigned32(speedText, speed)) || strtok(nullptr, " \t") ||
          joint < 1 || joint > 2 || degrees > 180 || speed < 1 || speed > 600 || !control_.servoReady()) { Serial.println("error: set 1|2 degrees [speed 1-600]"); return; }
      if (!control_.setJoint(joint - 1, static_cast<int>(degrees), speed)) Serial.printf("error: %s\n", control_.arm().error());
      else Serial.println("ok");
      return;
    }
    if (strtok(nullptr, " \t")) { Serial.println("error: unexpected arguments"); return; }
    const char* error = control_.local(commandFrom(name), millis());
    if (error) Serial.printf("error: %s\n", error); else { Serial.println("ok"); printStatus(); }
  }
  void readSerial(uint32_t now) {
    if (lineLength_ && static_cast<uint32_t>(now - lineStarted_) >= 2000) { lineLength_ = 0; lineOverflow_ = true; }
    for (int budget = 128; budget > 0 && Serial.available(); --budget) {
      const char c = static_cast<char>(Serial.read());
      if (c == '\r' || c == '\n') {
        if (lineOverflow_) Serial.println("error: oversized, invalid or expired serial line");
        else if (lineLength_) serialCommand();
        lineLength_ = 0; lineOverflow_ = false;
      } else if (c < 32 || c > 126 || lineLength_ == sizeof(line_) - 1) lineOverflow_ = true;
      else if (!lineOverflow_) { if (!lineLength_) lineStarted_ = now; line_[lineLength_++] = c; }
    }
  }
  scooper::LedcServos servos_;
  Control<scooper::LedcServos> control_;
  NetworkServer listener_;
  NetworkClient client_;
  HttpDecoder decoder_;
  char response_[768] = {}, line_[64] = {};
  size_t responseLength_ = 0, sent_ = 0, lineLength_ = 0;
  uint32_t lastWifiAttempt_ = 0, lineStarted_ = 0;
  bool active_ = false, listening_ = false, wifiConnected_ = false, lineOverflow_ = false;
};
}  // namespace companion
