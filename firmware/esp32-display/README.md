# Bullseye standalone BLE display

For display **and** scooper on this same S3, use the
[combined single-file firmware](../esp32-companion/README.md). This original
display-only sketch is preserved as a separate baseline. The combined firmware
uses one BLE connection owned by Mission Control for both functions, so stop the
separate `send-display.sh` process when connecting from the Scooper panel.

Open `bullseye_display/bullseye_display.ino` in Arduino IDE. This is firmware for
the **separate ESP32-S3 touchscreen**, not either board on the ELEGOO rover.

See [the complete setup, protocol, checks and Mac sender guide](../../docs/BLE_DISPLAY.md).
Keep the working DFRobot_GDL library and wiring. Touch controls only change local
pages; this sketch contains no Wi-Fi, vehicle networking or rover controls.

The display boots to the full-screen animated **Pixel bot Face** page, with no
text, buttons, telemetry or tabs. **Auto is on by default**, cycling Happy /
Curious / Grumpy / Sleepy / Idle every four seconds. Each expression also has its
own nonblocking animation. Swipe left/right for Distance and Link information;
swipe left from Face to reach Distance. Taps have no effect. No new libraries
are needed.

For the update, stop the Mac sender, close/reopen the sketch to load its added
headers, upload with the settings below, then restart the existing sender.

Settings: ESP32S3 Dev Module, Espressif core **3.3.12**, OPI 80 MHz flash,
**32 MB** flash, **OPI PSRAM**, **Default 4MB with spiffs**, USB CDC On Boot
**Disabled**, upload **115200** through the UART USB connector.

Offline native checks (Mac with Command Line Tools and cJSON):

```sh
/Users/faiyaz/Bullseye/firmware/esp32-display/tests/run-native.sh
```

`TouchInput.h` is an unchanged copy of the owner's working Onebox touch driver;
the bus adapter and reset sequence also come from that reference. See
[source provenance](../../docs/SOURCE_REFERENCES.md#standalone-ble-display).
Onebox itself and the rover's vendor firmware are preserved.
