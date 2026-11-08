// Bullseye scooper: two SG90 servos swing popsicle sticks together to squeeze an
// ore block up. Bench sketch for the ESP32-S3 display board.
//
// Power-up: both sticks go straight down. `squeeze`: down -> out 30° -> in 80°.
// Commands arrive over the UART USB serial port at 115200 baud, one per line.
// Mission Control's /bench page drives it the same way (see the README).
//
// Line protocol (keep in sync with mission-control/server/bullseye/scooper.py):
//   every command is answered with exactly one "ok" or "error: <reason>" line;
//   "state=<state> [<sequence>:<step>] left=<deg> right=<deg>" is printed on every
//   state or step change and before the "ok" for `status`. Other lines are chatter.
#include <Arduino.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include "ScooperConfig.h"
#include "Scooper.h"
#include "ServoOutput.h"

using scooper::State;

scooper::LedcServos servos;
scooper::Scooper<scooper::LedcServos> arm(servos);

char line[64];
size_t lineLength = 0;
bool lineOverflow = false;
State reportedState = State::Relaxed;
uint8_t reportedStep = 0;

void printStatus() {
  Serial.printf("state=%s", scooper::stateName(arm.state()));
  if (arm.state() == State::Sequence) Serial.printf(" %s:%s", arm.sequenceName(), arm.stepLabel());
  for (uint8_t i = 0; i < scooper::kJointCount; ++i) {
    Serial.printf(" %s=%d", scooper::kJoints[i].name, arm.angle(i));
  }
  Serial.println();
}

void printHelp() {
  Serial.println("Commands:");
  Serial.println("  stop | x               freeze now and cancel squeeze/release");
  Serial.println("  relax                  stop pulses; servos go limp");
  Serial.println("  home                   both sticks straight down");
  Serial.println("  squeeze                down, out 30 deg, then in 80 deg to lift the block");
  Serial.println("  release                out 30 deg, then straight down");
  Serial.printf("  set <1-%u> <deg> [dps]  move one stick (logical deg: 90 down, lower = out)\n",
                scooper::kJointCount);
  Serial.println("  status | help");
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
  if (!strcmp(word, "stop") || !strcmp(word, "x")) {
    arm.stop();
    reply(true);
  } else if (!strcmp(word, "relax")) {
    arm.relax();
    reply(true);
  } else if (!strcmp(word, "home")) {
    reply(arm.home());
  } else if (!strcmp(word, "squeeze") || !strcmp(word, "scoop")) {
    reply(arm.squeeze());
  } else if (!strcmp(word, "release")) {
    reply(arm.release());
  } else if (!strcmp(word, "set")) {
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
  } else if (!strcmp(word, "help") || !strcmp(word, "?")) {
    printHelp();
    reply(true);
  } else {
    Serial.printf("error: unknown command '%s' (try help)\n", word);
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
}

void loop() {
  readSerial();
  arm.update(millis());
  if (arm.state() != reportedState || arm.stepIndex() != reportedStep) {
    reportedState = arm.state();
    reportedStep = arm.stepIndex();
    printStatus();
  }
  delay(1);
}
