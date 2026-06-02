#include "adapters/ra_esp32_wake_word.h"

#include "esp_log.h"

static const char *TAG = "ra_esp32_wake_word";

void ra_esp32_wake_word_start(ra_device_client_t *client) {
    (void)client;
    ESP_LOGW(TAG, "WakeNet is not wired yet; use app/server manual wake path for first smoke test");
}
