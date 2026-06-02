#include "diagnostics/ra_esp32_diag.h"

#include "esp_log.h"

static const char *TAG = "ra_esp32_diag";

void ra_esp32_diag_log_snapshot(const ra_diagnostics_t *diagnostics) {
    if (diagnostics == NULL) {
        return;
    }
    ESP_LOGI(TAG,
             "diag registered=%d connection=%s conversation=%s sent_events=%u received_events=%u last_event=%s last_error=%s",
             diagnostics->registered,
             diagnostics->connection_state,
             diagnostics->conversation_state,
             (unsigned)diagnostics->sent_events,
             (unsigned)diagnostics->received_events,
             diagnostics->last_event_name,
             diagnostics->last_error);
}
