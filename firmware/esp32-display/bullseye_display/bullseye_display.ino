// Bullseye standalone BLE display. No Wi-Fi, rover commands, or vehicle code.
#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <DFRobot_GDL.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLE2902.h>
#if defined(CONFIG_NIMBLE_ENABLED)
#include <host/ble_hs_mbuf.h>
#endif
#include "DisplayJson.h"
#include "DisplayRenderer.h"
#include "DisplayInput.h"

DFRobot_ST7365P_320x480_HW_SPI screen(8, 10, 9, GDL_PIN_NC);
bullseye::Receiver receiver;
bullseye::Model model;
bullseye::Renderer renderer;
SemaphoreHandle_t stateMutex;
BLEServer* server = nullptr;
BLECharacteristic* ackCharacteristic = nullptr;
BLE2902* ackSubscription = nullptr;
bool connected = false, advertisePending = false;
int32_t activeConnection = -1;
uint32_t epoch = 0, disconnectedAt = 0;

// Exact-length, bounded touch access copied from the working Onebox adapter.
class TouchBus {
 public:
  bool read(uint8_t address, uint16_t reg, uint8_t regBytes, uint8_t* out, size_t count) {
    Wire.beginTransmission(address);
    if (regBytes == 2) Wire.write(static_cast<uint8_t>(reg >> 8));
    Wire.write(static_cast<uint8_t>(reg));
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(address, count, true) != count) {
      while (Wire.available()) Wire.read();
      return false;
    }
    for (size_t i = 0; i < count; ++i) out[i] = static_cast<uint8_t>(Wire.read());
    return true;
  }
  bool write(uint8_t address, uint16_t reg, uint8_t value) {
    Wire.beginTransmission(address);
    Wire.write(static_cast<uint8_t>(reg >> 8));
    Wire.write(static_cast<uint8_t>(reg));
    Wire.write(value);
    return Wire.endTransmission(true) == 0;
  }
};
TouchBus touchBus;
onebox::TouchController<TouchBus> touchController(touchBus);
onebox::TouchGestures gestures;
bool touchBusStarted = false, touchReady = false;
uint32_t lastTouch = 0, lastTouchAttempt = 0, lastRender = 0;

void beginTouch() {
  pinMode(6, OUTPUT); digitalWrite(6, LOW);
  pinMode(7, OUTPUT); digitalWrite(7, LOW);
  delay(10); digitalWrite(6, HIGH); delay(5);
  delay(50); pinMode(7, INPUT); delay(100);
  Wire.setTimeOut(5);
  touchBusStarted = Wire.begin(4, 5, 100000);
  touchReady = touchBusStarted && touchController.begin();
  lastTouchAttempt = millis();
  Serial.printf("Touch: %s\n", touchController.name());
}

class ServerCallbacks : public BLEServerCallbacks {
  void accept(BLEServer* owner, uint16_t id) {
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    const bool reject = connected;
    if (!reject) {
      connected = true; activeConnection = id;
      ++epoch; receiver.reset(); advertisePending = false;
    }
    xSemaphoreGive(stateMutex);
    if (reject) owner->disconnect(id);
  }
  void dropped(uint16_t id) {
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    if (activeConnection == id) {
      connected = false; activeConnection = -1; ++epoch; receiver.reset();
      advertisePending = true; disconnectedAt = millis();
    }
    xSemaphoreGive(stateMutex);
  }
#if defined(CONFIG_NIMBLE_ENABLED)
  void onConnect(BLEServer* owner, ble_gap_conn_desc* desc) override { accept(owner, desc->conn_handle); }
  void onDisconnect(BLEServer*, ble_gap_conn_desc* desc) override { dropped(desc->conn_handle); }
#else
  void onConnect(BLEServer* owner, esp_ble_gatts_cb_param_t* param) override { accept(owner, param->connect.conn_id); }
  void onDisconnect(BLEServer*, esp_ble_gatts_cb_param_t* param) override { dropped(param->disconnect.conn_id); }
#endif
};

class DataCallbacks : public BLECharacteristicCallbacks {
  void ingest(BLECharacteristic* characteristic, uint16_t id) {
    // getValue is bounded by the GATT attribute limit, and also works for the
    // core's execute-write callback. No JSON or display access in this callback.
    const String data = characteristic->getValue();
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    if (connected && activeConnection == id) {
      if (data.length() > 512) receiver.reject();
      else receiver.feed(reinterpret_cast<const uint8_t*>(data.c_str()), data.length(), millis(), epoch);
    }
    xSemaphoreGive(stateMutex);
  }
#if defined(CONFIG_NIMBLE_ENABLED)
  void onWrite(BLECharacteristic* characteristic, ble_gap_conn_desc* desc) override { ingest(characteristic, desc->conn_handle); }
#else
  void onWrite(BLECharacteristic* characteristic, esp_ble_gatts_cb_param_t* param) override { ingest(characteristic, param->write.conn_id); }
#endif
};

// State mutex held: bind the notification to the accepted connection, rather
// than broadcasting an old frame's ACK to a newly connected central.
void sendAckLocked(bullseye::Ack status, uint32_t sequence) {
  uint8_t bytes[8]; bullseye::encodeAck(bytes, status, sequence);
  ackCharacteristic->setValue(bytes, sizeof(bytes));
  if (connected && activeConnection >= 0) {
#if defined(CONFIG_NIMBLE_ENABLED)
    os_mbuf* buffer = ble_hs_mbuf_from_flat(bytes, sizeof(bytes));
    if (buffer) ble_gatts_notify_custom(static_cast<uint16_t>(activeConnection), ackCharacteristic->getHandle(), buffer);
#else
    if (!ackSubscription->getNotifications()) return;
    esp_ble_gatts_send_indicate(server->getGattsIf(), static_cast<uint16_t>(activeConnection),
                               ackCharacteristic->getHandle(), sizeof(bytes), bytes, false);
#endif
  }
}

void beginBle() {
  BLEDevice::init("Bullseye-Display");
  BLEDevice::setMTU(185);  // Preference only; sender uses the actual negotiated MTU.
  server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  BLEService* service = server->createService(bullseye::kServiceUuid);
  ackCharacteristic = service->createCharacteristic(bullseye::kAckUuid,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
#if defined(CONFIG_BLUEDROID_ENABLED)
  ackSubscription = new BLE2902();
  ackCharacteristic->addDescriptor(ackSubscription);
#endif
  uint8_t initial[8]; bullseye::encodeAck(initial, bullseye::Ack::Invalid, 0);
  ackCharacteristic->setValue(initial, sizeof(initial));
  BLECharacteristic* rx = service->createCharacteristic(bullseye::kRxUuid, BLECharacteristic::PROPERTY_WRITE);
  rx->setCallbacks(new DataCallbacks());
  service->start();
  BLEAdvertisementData advertised, response;
  advertised.setFlags(0x06);
  advertised.setCompleteServices(BLEUUID(bullseye::kServiceUuid));
  response.setName("Bullseye-Display");
  BLEAdvertising* advertising = server->getAdvertising();
  advertising->setAdvertisementData(advertised);
  advertising->setScanResponseData(response);
  advertising->setScanResponse(true);
  advertising->start();
  Serial.println("Bullseye BLE display ready; waiting for Mac sender.");
}

void setup() {
  Serial.begin(115200);
  stateMutex = xSemaphoreCreateMutex();
  if (!stateMutex) { Serial.println("Unable to allocate display mailbox mutex"); while (true) delay(1000); }
  SPI.begin(12, 13, 11, 10);
  screen.begin(8000000);
  screen.setRotation(3);
  beginTouch();
  renderer.render(screen, model, touchReady ? touchController.name() : "unavailable", millis());
  beginBle();
}

void loop() {
  uint32_t now = millis();
  bool advertise = false;
  xSemaphoreTake(stateMutex, portMAX_DELAY);
  receiver.expire(now);
  model.link(connected, epoch, now);
  const uint8_t error = receiver.takeError();
  if (error) { ++model.errors; if (connected) sendAckLocked(static_cast<bullseye::Ack>(error), 0); }
  if (advertisePending && !connected && static_cast<uint32_t>(now - disconnectedAt) >= 100) {
    advertise = true; advertisePending = false;
  }
  xSemaphoreGive(stateMutex);
  if (advertise) server->startAdvertising();

  for (uint8_t i = 0; i < 2; ++i) {
    bullseye::Frame frame;
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    const bool haveFrame = receiver.pop(frame);
    xSemaphoreGive(stateMutex);
    if (!haveFrame) break;
    bullseye::Sample sample;
    const bool valid = bullseye::decodeSample(frame, sample);
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    model.link(connected, epoch, millis());
    if (connected && frame.epoch == epoch) {
      const auto result = valid ? model.apply(sample, frame, millis()) : bullseye::Ack::Invalid;
      if (result != bullseye::Ack::Applied) ++model.errors;
      sendAckLocked(result, valid ? sample.seq : 0);
    }
    xSemaphoreGive(stateMutex);
  }

  now = millis();
  if (touchBusStarted && !touchReady && static_cast<uint32_t>(now - lastTouchAttempt) >= 5000) {
    lastTouchAttempt = now; touchReady = touchController.begin();
  }
  if (touchReady && static_cast<uint32_t>(now - lastTouch) >= 20) {
    lastTouch = now;
    const auto frame = touchController.poll();
    if (frame.state == onebox::TouchState::Error) {
      touchReady = false; lastTouchAttempt = now; gestures.cancel();
    } else {
      const auto gesture = gestures.update(frame, now);
      bullseye::handleGesture(model, gesture, now);
    }
  }
  if (static_cast<uint32_t>(now - lastRender) >= 100) {
    lastRender = now;
    renderer.render(screen, model, touchReady ? touchController.name() : "unavailable", now);
  }
  delay(1);
}
