#include "ws_control.h"
#include "protocol/protocol_adapter.h"
#include "ws_stream.h"
#include <string.h>
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_websocket_client.h"
#include "esp_log.h"

static const char *TAG = "ws_ctrl";

static const char *s_server_url = NULL;
static const char *s_device_id = NULL;
static char s_user_id[64] = {0};
static audio_chat_device_t *s_device = NULL;
static esp_websocket_client_handle_t s_client = NULL;

static ws_control_on_connected_cb_t s_on_connected = NULL;
static ws_control_on_disconnected_cb_t s_on_disconnected = NULL;
static ws_control_on_message_cb_t s_on_message = NULL;

static bool s_connected = false;
static bool s_reconnecting = false;

static esp_err_t send_text_event(const char *event_name, const char *payload, size_t len,
                                  const char *stream_id, const char *stream_type) {
    if (!s_client || !esp_websocket_client_is_connected(s_client)) {
        return ESP_FAIL;
    }

    const char *session_id = ws_stream_get_session_id();
    if (!session_id || !session_id[0]) session_id = s_device_id;
    char *json = protocol_adapter_create_event(event_name, payload, len, s_user_id[0] ? s_user_id : s_device_id,
                                               s_device_id, session_id, stream_id, stream_type);
    if (!json) return ESP_ERR_NO_MEM;

    int ret = esp_websocket_client_send_text(s_client, json, strlen(json), 1000 / portTICK_PERIOD_MS);
    free(json);
    return ret >= 0 ? ESP_OK : ESP_FAIL;
}

static void ws_event_handler(void *handler_args, esp_event_base_t event_base,
                              int32_t event_id, void *event_data) {
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;

    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "Connected");
        s_connected = true;
        // Send registration immediately on connect
        if (s_device) {
            char reg_payload[1024];
            int n = audio_chat_device_registration_json(s_device, reg_payload, sizeof(reg_payload));
            if (n > 0) {
                ESP_LOGI(TAG, "Registering...");
                send_text_event("control.device.register.requested", reg_payload, n, NULL, NULL);
            }
        }
        if (s_on_connected) s_on_connected();
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGI(TAG, "Disconnected");
        s_connected = false;
        if (s_on_disconnected) s_on_disconnected();
        break;

    case WEBSOCKET_EVENT_DATA:
        if (data->data_len > 0 && data->data_ptr) {
            if (data->payload_offset == 0) {
                ESP_LOGI(TAG, "WS DATA: op=0x%02x len=%d offset=%d total=%d",
                         data->op_code, data->data_len, data->payload_offset, data->payload_len);
                protocol_adapter_parse_event(data->data_ptr, data->data_len, s_on_message);
            } else {
                ESP_LOGW(TAG, "WS DATA FRAGMENT: offset=%d len=%d total=%d (ignored)",
                         data->payload_offset, data->data_len, data->payload_len);
            }
        }
        break;

    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGW(TAG, "Error");
        break;

    default:
        break;
    }
}

esp_err_t ws_control_init(const char *server_url, const char *device_id,
                          audio_chat_device_t *device) {
    s_server_url = server_url;
    s_device_id = device_id;
    s_device = device;
    if (device && device->user_id[0]) {
        snprintf(s_user_id, sizeof(s_user_id), "%s", device->user_id);
    } else {
        snprintf(s_user_id, sizeof(s_user_id), "user-device-%s", device_id);
    }

    ESP_LOGI(TAG, "Init: %s", server_url);
    ESP_LOGI(TAG, "Heap: free=%u, internal=%u, largest_block=%u",
             (unsigned)esp_get_free_heap_size(),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));

    esp_websocket_client_config_t config = {
        .uri = server_url,
        .reconnect_timeout_ms = 10000,
        .network_timeout_ms = 10000,
        .buffer_size = 1024,
        .task_stack = 8192,
        .task_prio = 5,
        .task_core_id = 0,
        .task_core_id_set = true,
        .disable_auto_reconnect = true,
        .disable_pingpong_discon = false,
    };

    s_client = esp_websocket_client_init(&config);
    if (!s_client) {
        ESP_LOGE(TAG, "Init failed");
        return ESP_FAIL;
    }

    esp_err_t ret = esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY, ws_event_handler, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Register events failed: %d", ret);
        return ret;
    }

    ESP_LOGI(TAG, "Init OK");
    return ESP_OK;
}

esp_err_t ws_control_set_callbacks(ws_control_on_connected_cb_t on_connected,
                                    ws_control_on_disconnected_cb_t on_disconnected,
                                    ws_control_on_message_cb_t on_message) {
    s_on_connected = on_connected;
    s_on_disconnected = on_disconnected;
    s_on_message = on_message;
    return ESP_OK;
}

esp_err_t ws_control_send_event(const char *event_name, const char *payload, size_t len) {
    return send_text_event(event_name, payload, len, NULL, NULL);
}

esp_err_t ws_control_send_event_with_stream(const char *event_name, const char *payload, size_t len,
                                              const char *stream_id, const char *stream_type) {
    return send_text_event(event_name, payload, len, stream_id, stream_type);
}

esp_err_t ws_control_send_binary(const uint8_t *data, size_t len) {
    if (!s_client || !esp_websocket_client_is_connected(s_client)) return ESP_FAIL;
    int ret = esp_websocket_client_send_bin(s_client, (const char *)data, len, 1000 / portTICK_PERIOD_MS);
    return ret >= 0 ? ESP_OK : ESP_FAIL;
}

bool ws_control_is_connected(void) {
    return s_connected;
}

esp_err_t ws_control_disconnect(void) {
    s_connected = false;
    if (s_client) {
        esp_websocket_client_stop(s_client);
        esp_websocket_client_destroy(s_client);
        s_client = NULL;
    }
    return ESP_OK;
}

esp_err_t ws_control_reconnect(void) {
    if (s_reconnecting) {
        ESP_LOGI(TAG, "Already reconnecting, skip");
        return ESP_OK;
    }
    s_reconnecting = true;

    if (!s_client) {
        esp_err_t ret = ws_control_init(s_server_url, s_device_id, s_device);
        if (ret == ESP_OK) {
            ret = esp_websocket_client_start(s_client);
        }
        s_reconnecting = false;
        return ret;
    }

    ESP_LOGI(TAG, "Reconnecting...");
    esp_err_t ret = esp_websocket_client_start(s_client);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Start failed (%d), re-init...", ret);
        esp_websocket_client_destroy(s_client);
        s_client = NULL;
        vTaskDelay(pdMS_TO_TICKS(1000));
        ret = ws_control_init(s_server_url, s_device_id, s_device);
        if (ret == ESP_OK) {
            ret = esp_websocket_client_start(s_client);
        }
    }
    s_reconnecting = false;
    return ret;
}

esp_err_t ws_control_task_start(void) {
    ESP_LOGI(TAG, "Starting...");
    esp_err_t ret = esp_websocket_client_start(s_client);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Start failed: %d", ret);
        return ret;
    }
    ESP_LOGI(TAG, "Started");
    return ESP_OK;
}
