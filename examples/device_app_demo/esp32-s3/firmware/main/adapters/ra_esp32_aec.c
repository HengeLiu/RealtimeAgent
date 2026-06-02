#include "adapters/ra_esp32_aec.h"

#include "esp_log.h"

static const char *TAG = "ra_esp32_aec";
static size_t s_reference_bytes = 0;
static size_t s_output_bytes = 0;

void ra_esp32_aec_record_reference(const uint8_t *pcm, size_t size) {
    (void)pcm;
    s_reference_bytes += size;
    if (s_reference_bytes == size) {
        ESP_LOGW(TAG, "AEC reference counter is active, but ESP-SR AEC processing is not wired yet");
    }
}

void ra_esp32_aec_record_output(const uint8_t *pcm, size_t size) {
    (void)pcm;
    s_output_bytes += size;
}
