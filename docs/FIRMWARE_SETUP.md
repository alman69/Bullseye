# Firmware setup notes

This repository contains the source for the ELEGOO Smart Robot Car Kit V4.0, **2023-02-01** package, for the hardware installed on this robot:

- Controller: ELEGOO Arduino UNO R3
- Motor driver: TB6612
- IMU: MPU6050 (GY-521)
- Shield: SmartCar-Shield V1.1
- Camera: ESP32-WROVER

## Arduino UNO

Open `firmware/arduino-uno/SmartRobotCarV4.0_V1_20230201/SmartRobotCarV4.0_V1_20230201.ino` in Arduino IDE. Keep the entire sketch directory together; its `.cpp` and `.h` files are needed.

- Board: **Arduino Uno** (Arduino AVR Boards)
- Port: the serial port of the UNO
- Physical shield switch: **Upload** while flashing the UNO, **Cam** for normal ESP32 communication
- Library dependencies from the original vendor bundle include **FastLED** and **NewPing**. **Servo** and **Wire** are from the Arduino ecosystem/core. IRremote, MPU6050, I2Cdev and ArduinoJson sources are already in the sketch directory. The original vendor ZIP also has an `addLibrary/` folder of library archives, which is not committed here; use the packages from your original kit archive when necessary for version compatibility.

The original sketch uploaded successfully to the physical UNO with the expansion shield removed. **A clean build of this GitHub checkout has not yet been verified.** The UNO was already using about 96% of its available flash in that successful build, so plan to keep heavy AI/navigation processing off the UNO.

If uploads fail with `not in sync`, disconnect all power, carefully remove the shield, then retry directly via USB; this solved the initial upload failure. Reconnect the shield only while unpowered.

## ESP32-WROVER camera

Open `firmware/esp32-camera/ESP32_CameraServer_AP_20220120/ESP32_CameraServer_AP_20220120.ino`.

The original `Notes.txt` says to use **ESP32 Dev Module**, **Huge APP (3MB No OTA/1MB SPIFFS)** partitioning, and **PSRAM enabled**. This is vendor-provided historical firmware, and compatibility with newer ESP32 board packages has **not** been verified. Do not flash the camera until the toolchain, pinout and recovery procedure are confirmed.

The source creates the camera Wi-Fi access point and listens on **TCP port 100** to relay JSON commands to/from the Arduino through Serial2. The UNO's serial-command handler is in `ApplicationFunctionSet_xxx0.cpp`.

## Physical safety

For first motor tests, lift the wheels off the table. Turn off battery power and unplug USB before moving wires or reseating the expansion shield. Verify manual forward/backward motor direction before investigating obstacle avoidance.

## Known observed behavior

- IR remote Mode **2** rotates the ultrasonic sensor, then sometimes drives backward. This has **not** been diagnosed; possibilities include expected obstacle recovery, bad ultrasonic distances or wheel direction. Do not make an unverified polarity/code change without testing.
- Firmware source here is an unmodified vendor baseline for future development, **not** a new working navigation implementation.
