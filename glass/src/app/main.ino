#include "../glass_api/transport/server_client.h"
#include "../infra/logging/logger.h"

ServerClient g_server_client;

void setup() {
  Serial.begin(115200);
  log_event("glass.bootstrap.start");
  g_server_client.connect();
  g_server_client.registerDevice("dev_glass_001", "masked_token");
  log_event("glass.bootstrap.ready");
}

void loop() {
  g_server_client.tick(millis());
  // 第一阶段骨架：后续接入 sensor_hub / actuator_hub 调度。
  delay(50);
}
