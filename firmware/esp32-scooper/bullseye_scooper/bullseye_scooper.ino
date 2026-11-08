// Bullseye scooper: two SG90 servos swing popsicle sticks together to squeeze an
// ore block up. Runs on the ESP32-S3 display board.
//
// Power-up: both sticks go straight down. `squeeze`: down -> out 30° -> in 80°.
//
// Mission Control drives it over Wi-Fi: the board joins the rover's ELEGOO access
// point at a fixed address (WifiConfig.h) and serves a tiny HTTP API. Keep it in
// sync with mission-control/server/bullseye/scooper.py:
//   GET  /status               -> 200 {"state","sequence","step","angles":{...}}
//   POST /command?name=<cmd>   -> 200 {"ok":true,"status":{...}}
//                                 409 {"ok":false,"error":"...","status":{...}} (refused)
//                                 400 {"ok":false,"error":"..."} (unknown command)
//   <cmd> = squeeze | release | home | stop | relax
//
// The USB serial port (115200 baud) takes the same commands for bench testing in the
// Serial Monitor, plus `set`, `status`, `wifi` and `help`.
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include "ScooperConfig.h"
#include "Scooper.h"
#include "ServoOutput.h"
#include "WifiConfig.h"

using scooper::State;

scooper::LedcServos servos;
scooper::Scooper<scooper::LedcServos> arm(servos);
WebServer http(scooper::kHttpPort);

char line[64];
size_t lineLength = 0;
bool lineOverflow = false;
State reportedState = State::Relaxed;
uint8_t reportedStep = 0;
bool httpStarted = false;
wl_status_t reportedWifi = WL_IDLE_STATUS;
uint32_t lastWifiAttempt = 0;

enum class Outcome : uint8_t { Ok, Refused, Unknown };

// The commands both Wi-Fi and serial accept. Refused leaves the reason in arm.error().
Outcome runNamed(const char* name) {
  if (!strcmp(name, "stop") || !strcmp(name, "x")) {
    arm.stop();
    return Outcome::Ok;
  }
  if (!strcmp(name, "relax")) {
    arm.relax();
    return Outcome::Ok;
  }
  bool ok;
  if (!strcmp(name, "home")) ok = arm.home();
  else if (!strcmp(name, "squeeze") || !strcmp(name, "scoop")) ok = arm.squeeze();
  else if (!strcmp(name, "release")) ok = arm.release();
  else return Outcome::Unknown;
  return ok ? Outcome::Ok : Outcome::Refused;
}

// ---- Status reporting --------------------------------------------------------

void printStatus() {
  Serial.printf("state=%s", scooper::stateName(arm.state()));
  if (arm.state() == State::Sequence) Serial.printf(" %s:%s", arm.sequenceName(), arm.stepLabel());
  for (uint8_t i = 0; i < scooper::kJointCount; ++i) {
    Serial.printf(" %s=%d", scooper::kJoints[i].name, arm.angle(i));
  }
  Serial.println();
}

void appendJsonText(String& json, const char* text) {  // Fixed names only: no escaping needed.
  json += "\"";
  json += text;
  json += "\"";
}

String statusJson() {
  const bool running = arm.state() == State::Sequence;
  String json = "{\"state\":";
  appendJsonText(json, scooper::stateName(arm.state()));
  json += ",\"sequence\":";
  if (running) appendJsonText(json, arm.sequenceName());
  else json += "null";
  json += ",\"step\":";
  if (running) appendJsonText(json, arm.stepLabel());
  else json += "null";
  json += ",\"angles\":{";
  for (uint8_t i = 0; i < scooper::kJointCount; ++i) {
    if (i) json += ",";
    json += "\"";
    json += scooper::kJoints[i].name;
    json += "\":";
    json += arm.angle(i);
  }
  json += "}}";
  return json;
}

// ---- Wi-Fi + HTTP ------------------------------------------------------------

IPAddress address(const uint8_t (&parts)[4]) { return IPAddress(parts[0], parts[1], parts[2], parts[3]); }

void joinWifi() {
  lastWifiAttempt = millis();
  WiFi.disconnect();
  WiFi.begin(scooper::kWifiSsid, scooper::kWifiPassword[0] ? scooper::kWifiPassword : nullptr);
}

void beginWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // No modem sleep: button presses answer without extra delay.
  WiFi.setAutoReconnect(true);
  if (!WiFi.config(address(scooper::kScooperIp), address(scooper::kGatewayIp), address(scooper::kSubnetMask))) {
    Serial.println("Wi-Fi: fixed address setup failed.");
  }
  joinWifi();
  Serial.printf("Wi-Fi: joining %s as %s\n", scooper::kWifiSsid, address(scooper::kScooperIp).toString().c_str());
}

void sendJson(int code, const String& body) { http.send(code, "application/json", body); }

void handleStatus() { sendJson(200, statusJson()); }

void handleCommand() {
  if (!http.hasArg("name")) {
    sendJson(400, "{\"ok\":false,\"error\":\"missing ?name=\"}");
    return;
  }
  String name = http.arg("name");
  name.toLowerCase();
  if (name == "x") name = "";  // The serial shortcut only; Wi-Fi uses the full names.
  switch (runNamed(name.c_str())) {
    case Outcome::Ok:
      sendJson(200, String("{\"ok\":true,\"status\":") + statusJson() + "}");
      break;
    case Outcome::Refused:  // Error texts are fixed strings in Scooper.h with no quotes.
      sendJson(409, String("{\"ok\":false,\"error\":\"") + arm.error() + "\",\"status\":" + statusJson() + "}");
      break;
    case Outcome::Unknown:
      sendJson(400, "{\"ok\":false,\"error\":\"unknown command\"}");
      break;
  }
}

void serviceWifi(uint32_t now) {
  const wl_status_t wifi = WiFi.status();
  if (wifi != reportedWifi) {
    reportedWifi = wifi;
    if (wifi == WL_CONNECTED) {
      Serial.printf("Wi-Fi: connected. Mission Control can reach http://%s\n", WiFi.localIP().toString().c_str());
      if (!httpStarted) {
        http.on("/status", HTTP_GET, handleStatus);
        http.on("/command", HTTP_POST, handleCommand);
        http.onNotFound([] { sendJson(404, "{\"ok\":false,\"error\":\"not found\"}"); });
        http.begin();
        httpStarted = true;
      }
    } else {
      Serial.println("Wi-Fi: not connected (is the rover on?). Servos still work over USB serial.");
    }
  }
  if (wifi != WL_CONNECTED && now - lastWifiAttempt >= scooper::kWifiRetryMs) joinWifi();
  if (httpStarted) http.handleClient();
}

// ---- USB serial (bench testing) ----------------------------------------------

void printHelp() {
  Serial.println("Commands (USB serial; Wi-Fi accepts squeeze/release/home/stop/relax):");
  Serial.println("  stop | x               freeze now and cancel squeeze/release");
  Serial.println("  relax                  stop pulses; servos go limp");
  Serial.println("  home                   both sticks straight down");
  Serial.println("  squeeze                down, out 30 deg, then in 80 deg to lift the block");
  Serial.println("  release                out 30 deg, then straight down");
  Serial.printf("  set <1-%u> <deg> [dps]  move one stick (logical deg: 90 down, lower = out)\n",
                scooper::kJointCount);
  Serial.println("  status | wifi | help");
  for (uint8_t i = 0; i < scooper::kJointCount; ++i) {
    const auto& j = scooper::kJoints[i];
    Serial.printf("  stick %u: %s on GPIO%u, limits %u-%u, home %u, trim %d%s\n", i + 1, j.name,
                  j.pin, j.minDeg, j.maxDeg, j.homeDeg, j.trimDeg, j.inverted ? ", inverted" : "");
  }
}

void reply(bool ok) {
  if (ok) Serial.println("ok");
  else Serial.printf("error: %s\n", arm.error());
}

bool parseNumber(const char* text, long& out) {
  if (!text) return false;
  char* end = nullptr;
  out = strtol(text, &end, 10);
  return end != text && *end == '\0';
}

void handle(char* command) {
  for (char* p = command; *p; ++p) *p = static_cast<char>(tolower(static_cast<unsigned char>(*p)));
  const char* word = strtok(command, " \t");
  if (!word) return;
  if (!strcmp(word, "set")) {
    long stick = 0, deg = 0, speed = scooper::kDefaultDegPerSec;
    const char* speedText = nullptr;
    if (!parseNumber(strtok(nullptr, " \t"), stick) || !parseNumber(strtok(nullptr, " \t"), deg) ||
        ((speedText = strtok(nullptr, " \t")) && !parseNumber(speedText, speed)) ||
        speed < 1 || speed > 600 || stick < 1 || stick > scooper::kJointCount) {
      Serial.printf("error: usage: set <1-%u> <deg> [degrees/second 1-600]\n", scooper::kJointCount);
      return;
    }
    reply(arm.setJoint(static_cast<uint8_t>(stick - 1), static_cast<int>(deg), static_cast<uint16_t>(speed)));
  } else if (!strcmp(word, "status")) {
    printStatus();
    reply(true);
  } else if (!strcmp(word, "wifi")) {
    Serial.printf("wifi=%s ssid=%s ip=%s\n", WiFi.status() == WL_CONNECTED ? "connected" : "disconnected",
                  scooper::kWifiSsid, WiFi.localIP().toString().c_str());
    reply(true);
  } else if (!strcmp(word, "help") || !strcmp(word, "?")) {
    printHelp();
    reply(true);
  } else {
    const Outcome outcome = runNamed(word);
    if (outcome == Outcome::Unknown) Serial.printf("error: unknown command '%s' (try help)\n", word);
    else reply(outcome == Outcome::Ok);
  }
}

void readSerial() {
  while (Serial.available()) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\r' || c == '\n') {
      if (lineOverflow) Serial.println("error: line too long");
      else if (lineLength) {
        line[lineLength] = '\0';
        handle(line);
      }
      lineLength = 0;
      lineOverflow = false;
    } else if (lineLength < sizeof(line) - 1) {
      line[lineLength++] = c;
    } else {
      lineOverflow = true;
    }
  }
}

// ---- Setup / loop ------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  // Initialization: start PWM with both sticks straight down before anything else.
  const bool started = arm.begin(millis());
  delay(200);
  Serial.println("\nBullseye scooper. SG90 power must come from a separate 5 V supply sharing GND.");
  if (!started) Serial.printf("Config error: %s. Servos left off.\n", arm.error());
  printHelp();
  printStatus();
  reportedState = arm.state();
  beginWifi();
}

void loop() {
  const uint32_t now = millis();
  readSerial();
  serviceWifi(now);
  arm.update(millis());
  if (arm.state() != reportedState || arm.stepIndex() != reportedStep) {
    reportedState = arm.state();
    reportedStep = arm.stepIndex();
    printStatus();
  }
  delay(1);
}
