// Included by the single-file generator after the display's global declarations.
BLECharacteristic* controlStatusCharacteristic = nullptr;
BLE2902* controlSubscription = nullptr;
uint32_t lastControlStatus = 0;

class ControlCallbacks : public BLECharacteristicCallbacks {
  void ingest(BLECharacteristic* characteristic, uint16_t id) {
    const String data = characteristic->getValue();
    xSemaphoreTake(stateMutex, portMAX_DELAY);
    if (connected && activeConnection == id)
      companionController.mailbox.feed(reinterpret_cast<const uint8_t*>(data.c_str()), data.length(), epoch);
    xSemaphoreGive(stateMutex);
  }
#if defined(CONFIG_NIMBLE_ENABLED)
  void onWrite(BLECharacteristic* characteristic, ble_gap_conn_desc* desc) override { ingest(characteristic, desc->conn_handle); }
#else
  void onWrite(BLECharacteristic* characteristic, esp_ble_gatts_cb_param_t* param) override { ingest(characteristic, param->write.conn_id); }
#endif
};

// Main loop + state mutex held: never acknowledge a command to a later central.
void sendControlStatusLocked(companion::Result result, uint32_t reply) {
  uint8_t bytes[20]; companionController.status(bytes, result, reply);
  controlStatusCharacteristic->setValue(bytes, sizeof(bytes));
  if (!connected || activeConnection < 0) return;
#if defined(CONFIG_NIMBLE_ENABLED)
  os_mbuf* buffer = ble_hs_mbuf_from_flat(bytes, sizeof(bytes));
  if (buffer) ble_gatts_notify_custom(static_cast<uint16_t>(activeConnection), controlStatusCharacteristic->getHandle(), buffer);
#else
  if (!controlSubscription->getNotifications()) return;
  esp_ble_gatts_send_indicate(server->getGattsIf(), static_cast<uint16_t>(activeConnection),
                            controlStatusCharacteristic->getHandle(), sizeof(bytes), bytes, false);
#endif
}

void beginControlGatt(BLEService* service) {
  controlStatusCharacteristic = service->createCharacteristic(companion::kStatusUuid,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
#if defined(CONFIG_BLUEDROID_ENABLED)
  controlSubscription = new BLE2902(); controlStatusCharacteristic->addDescriptor(controlSubscription);
#endif
  uint8_t bytes[20]; companionController.status(bytes); controlStatusCharacteristic->setValue(bytes, sizeof(bytes));
  auto* command = service->createCharacteristic(companion::kCommandUuid, BLECharacteristic::PROPERTY_WRITE);
  command->setCallbacks(new ControlCallbacks());
}

void pollControlGatt(uint32_t now) {
  xSemaphoreTake(stateMutex, portMAX_DELAY);
  companionController.link(connected, epoch, now);
  const auto error = companionController.mailbox.takeError();
  if (connected && error != companion::Result::Status) sendControlStatusLocked(error, 0);
  for (uint8_t i = 0; i < 2; ++i) {
    companion::CommandFrame frame;
    if (!companionController.mailbox.pop(frame)) break;
    if (connected && frame.epoch == epoch) {
      const auto result = companionController.execute(frame, now);
      sendControlStatusLocked(result, companion::read32(frame.bytes + 8));
    }
  }
  if (connected && static_cast<uint32_t>(now - lastControlStatus) >= 500) {
    lastControlStatus = now; sendControlStatusLocked(companion::Result::Status, 0);
  }
  xSemaphoreGive(stateMutex);
}
