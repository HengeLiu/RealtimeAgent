#include "ws_stream.h"
#include "../protocol/audio_chat_stream.h"
#include "../drivers/audio.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_websocket_client.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_heap_caps.h"

static const char *TAG = "ws_stream";

static esp_websocket_client_handle_t s_stream_client = NULL;
static bool s_stream_connected = false;
static bool s_initialized = false;

static char s_user_id[64] = {0};
static char s_session_id[64] = {0};
static char s_device_id[64] = {0};
static char s_input_stream_id[64] = {0};

static char s_stream_uri[256] = {0};
static TaskHandle_t s_reconnect_task = NULL;

// Fragment accumulation buffer for incoming binary frames (allocated in PSRAM)
#define RX_FRAME_BUF_SIZE 8192
static uint8_t *s_rx_frame_buf = NULL;
static int s_rx_frame_offset = 0;

static void reconnect_task(void *pvParameters);

static void websocket_stream_event_handler(void *handler_args, esp_event_base_t event_base,
                                         int32_t event_id, void *event_data) {
    (void)handler_args;
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;

    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "Stream WEBSOCKET_EVENT_CONNECTED");
        s_stream_connected = true;
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGI(TAG, "Stream WEBSOCKET_EVENT_DISCONNECTED");
        s_stream_connected = false;
        break;
    case WEBSOCKET_EVENT_DATA:
        // Log ALL incoming data for debugging
        {
            static int s_event_data_count = 0;
            s_event_data_count++;
            if (s_event_data_count <= 10 || s_event_data_count % 100 == 0) {
                ESP_LOGI(TAG, "WEBSOCKET_EVENT_DATA #%d: op=0x%02x len=%d offset=%d total=%d",
                         s_event_data_count, data->op_code, data->data_len,
                         data->payload_offset, data->payload_len);
            }
        }
        // Handle incoming binary frames from server (speaker audio)
        if (data->op_code == 0x02 && data->data_len > 0) {
            // Accumulate fragments - only decode when full frame is received
            if (data->payload_offset == 0) {
                s_rx_frame_offset = 0;
            }

            if (s_rx_frame_offset + data->data_len <= RX_FRAME_BUF_SIZE) {
                memcpy(s_rx_frame_buf + s_rx_frame_offset, data->data_ptr, data->data_len);
                s_rx_frame_offset += data->data_len;
            } else {
                ESP_LOGW(TAG, "RX binary frame too large: offset=%d len=%d total=%d",
                         s_rx_frame_offset, data->data_len, data->payload_len);
                s_rx_frame_offset = 0;
                break;
            }

            // Only decode when we have the complete frame
            if (s_rx_frame_offset >= data->payload_len) {
                static int s_rx_count = 0;
                s_rx_count++;
                if (s_rx_count <= 3 || s_rx_count % 50 == 0) {
                    ESP_LOGI(TAG, "RX binary #%d: %d bytes complete", s_rx_count, s_rx_frame_offset);
                }

                char header_json[512];
                const uint8_t *payload = NULL;
                size_t payload_size = 0;

                if (audio_chat_stream_decode(s_rx_frame_buf, s_rx_frame_offset,
                                              header_json, sizeof(header_json),
                                              &payload, &payload_size) == 0) {
                    if (payload_size > 0 && payload != NULL) {
                        if (s_rx_count <= 5) {
                            ESP_LOGI(TAG, "Decoded #%d: payload=%d bytes, header=%.120s", s_rx_count, (int)payload_size, header_json);
                        }
                        // Extract sample rate from header and reconfigure speaker if needed
                        const char *sr_key = strstr(header_json, "\"sample_rate\":");
                        if (sr_key) {
                            int rate = atoi(sr_key + 14);
                            if (rate > 0 && s_rx_count <= 3) {
                                ESP_LOGI(TAG, "Speaker rate from header: %d (raw=%.30s)", rate, sr_key);
                            }
                            if (rate > 0) {
                                audio_speaker_set_rate(rate);
                            }
                        } else {
                            if (s_rx_count <= 3) {
                                ESP_LOGW(TAG, "No sample_rate in header: %.120s", header_json);
                            }
                        }
                        esp_err_t feed_ret = audio_speaker_feed(payload, payload_size);
                        if (feed_ret != ESP_OK) {
                            ESP_LOGW(TAG, "audio_speaker_feed FAILED ret=%d payload=%d", feed_ret, (int)payload_size);
                        }
                    } else {
                        ESP_LOGW(TAG, "Decoded but payload empty: payload_size=%d", (int)payload_size);
                    }
                } else {
                    ESP_LOGW(TAG, "Failed to decode StreamChunk (%d bytes, first4=%02x%02x%02x%02x)",
                             s_rx_frame_offset,
                             s_rx_frame_buf[0], s_rx_frame_buf[1],
                             s_rx_frame_buf[2], s_rx_frame_buf[3]);
                }
                s_rx_frame_offset = 0;
            }
        } else if (data->op_code == 0x01 && data->data_len > 0) {
            // Text frame - server error events (system.error.raised)
            static char s_text_buf[512];
            int copy_len = data->data_len < (int)sizeof(s_text_buf) - 1 ? data->data_len : (int)sizeof(s_text_buf) - 1;
            if (data->payload_offset == 0) {
                memcpy(s_text_buf, data->data_ptr, copy_len);
                s_text_buf[copy_len] = '\0';
            } else {
                // Continuation frame - append
                int cur_len = strlen(s_text_buf);
                int remain = (int)sizeof(s_text_buf) - 1 - cur_len;
                if (remain > 0) {
                    int append_len = data->data_len < remain ? data->data_len : remain;
                    memcpy(s_text_buf + cur_len, data->data_ptr, append_len);
                    s_text_buf[cur_len + append_len] = '\0';
                }
            }
            // Only process on final fragment
            if (data->payload_offset + data->data_len >= data->payload_len) {
                ESP_LOGW(TAG, "Server text: %.200s%s", s_text_buf,
                         strlen(s_text_buf) > 200 ? "..." : "");
                // Try to extract error_type for quick identification
                const char *err_type = strstr(s_text_buf, "\"error_type\"");
                if (err_type) {
                    ESP_LOGE(TAG, "Server error: %.120s", err_type);
                }
            }
        } else if (data->data_len > 0) {
            ESP_LOGW(TAG, "RX non-binary: op=0x%02x len=%d", data->op_code, data->data_len);
        }
        break;
    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGE(TAG, "Stream WEBSOCKET_EVENT_ERROR");
        break;
    default:
        break;
    }
}

static void reconnect_task(void *pvParameters) {
    (void)pvParameters;
    ESP_LOGI(TAG, "Reconnect task started");

    // Wait for initial connection attempt to complete
    vTaskDelay(pdMS_TO_TICKS(5000));

    while (s_initialized) {
        vTaskDelay(pdMS_TO_TICKS(3000));

        // Auto-reconnect if stream WS disconnected during active session
        if (s_initialized && !s_stream_connected && s_stream_client) {
            ESP_LOGI(TAG, "Stream WS disconnected, reconnecting...");
            esp_websocket_client_stop(s_stream_client);
            vTaskDelay(pdMS_TO_TICKS(500));
            esp_err_t ret = esp_websocket_client_start(s_stream_client);
            if (ret != ESP_OK) {
                ESP_LOGW(TAG, "Stream WS reconnect failed: %d", ret);
            } else {
                ESP_LOGI(TAG, "Stream WS reconnecting...");
            }
        }
    }

    s_reconnect_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t ws_stream_init(const char *server_url, const char *device_id, const char *stream_id) {
    // Allocate rx frame buffer in PSRAM if not already done
    if (!s_rx_frame_buf) {
        s_rx_frame_buf = (uint8_t *)heap_caps_malloc(RX_FRAME_BUF_SIZE, MALLOC_CAP_SPIRAM);
        if (!s_rx_frame_buf) {
            ESP_LOGE(TAG, "Failed to allocate rx_frame_buf in PSRAM");
            return ESP_ERR_NO_MEM;
        }
        ESP_LOGI(TAG, "RX frame buf: %d bytes at %p (PSRAM)", RX_FRAME_BUF_SIZE, s_rx_frame_buf);
    }

    // Clean up any existing connection first
    if (s_stream_client) {
        ESP_LOGI(TAG, "Cleaning up previous stream WS client");
        esp_websocket_client_stop(s_stream_client);
        vTaskDelay(pdMS_TO_TICKS(1000));  // Wait for internal task to fully exit
        esp_websocket_client_destroy(s_stream_client);
        s_stream_client = NULL;
        s_stream_connected = false;
        vTaskDelay(pdMS_TO_TICKS(500));  // Extra settle for memory defrag
    }
    if (s_reconnect_task) {
        vTaskDelete(s_reconnect_task);
        s_reconnect_task = NULL;
    }

    snprintf(s_user_id, sizeof(s_user_id), "user-device-%s", device_id);
    snprintf(s_device_id, sizeof(s_device_id), "%s", device_id);
    snprintf(s_session_id, sizeof(s_session_id), "%s", device_id);
    if (stream_id) {
        snprintf(s_input_stream_id, sizeof(s_input_stream_id), "%s", stream_id);
    }

    snprintf(s_stream_uri, sizeof(s_stream_uri), "%s?device_id=%s", server_url, device_id);
    esp_websocket_client_config_t stream_config = {
        .uri = s_stream_uri,
        .reconnect_timeout_ms = 10000,
        .network_timeout_ms = 10000,
        .buffer_size = 4096,
        .task_stack = 3072,
        .task_prio = 4,
        .task_core_id = 1,
        .task_core_id_set = true,
        .disable_auto_reconnect = false,
    };
    s_stream_client = esp_websocket_client_init(&stream_config);
    if (!s_stream_client) {
        ESP_LOGE(TAG, "Failed to create stream websocket client");
        return ESP_FAIL;
    }
    esp_websocket_register_events(s_stream_client, WEBSOCKET_EVENT_ANY,
                                  websocket_stream_event_handler, NULL);
    esp_websocket_client_start(s_stream_client);

    s_initialized = true;
    audio_chat_stream_reset_seq();
    xTaskCreatePinnedToCore(&reconnect_task, "ws_reconnect", 2048, NULL, 3, &s_reconnect_task, 0);

    ESP_LOGI(TAG, "Stream WebSocket client initialized (user=%s, session=%s)",
             s_user_id, s_session_id);
    return ESP_OK;
}

const char* ws_stream_get_user_id(void) {
    return s_user_id;
}

const char* ws_stream_get_session_id(void) {
    return s_session_id;
}

void ws_stream_update_session(const char *new_session_id) {
    if (new_session_id) {
        snprintf(s_session_id, sizeof(s_session_id), "%s", new_session_id);
        audio_chat_stream_reset_seq();
        ESP_LOGI(TAG, "Session updated: %s", s_session_id);
    }
}

esp_err_t ws_stream_send_audio(const uint8_t *data, size_t len) {
    if (!s_stream_client) {
        return ESP_FAIL;
    }
    if (!esp_websocket_client_is_connected(s_stream_client)) {
        static int s_drop_count = 0;
        s_drop_count++;
        if (s_drop_count <= 3 || s_drop_count % 100 == 0) {
            ESP_LOGW(TAG, "Stream WS not connected, dropping audio #%d", s_drop_count);
        }
        return ESP_FAIL;
    }

    static uint8_t *s_encoded_buf = NULL;
    if (!s_encoded_buf) {
        s_encoded_buf = (uint8_t *)heap_caps_malloc(4096, MALLOC_CAP_SPIRAM);
        if (!s_encoded_buf) return ESP_FAIL;
    }
    size_t written = 0;

    if (audio_chat_stream_encode(s_input_stream_id[0] ? s_input_stream_id : "audio", "sensor.mic", data, len,
                                  s_user_id, s_session_id,
                                  s_encoded_buf, 4096, &written) != 0) {
        return ESP_FAIL;
    }

    int ret = esp_websocket_client_send_bin(s_stream_client, (const char *)s_encoded_buf, written,
                                             1000 / portTICK_PERIOD_MS);
    return ret >= 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t ws_stream_send_final(const char *reason) {
    if (!s_stream_client || !esp_websocket_client_is_connected(s_stream_client)) {
        return ESP_FAIL;
    }

    char metadata[128];
    snprintf(metadata, sizeof(metadata), "{\"reason\":\"%s\"}", reason ? reason : "closed");

    static uint8_t s_final_buf[512];
    size_t written = 0;

    if (audio_chat_stream_encode_ex(s_input_stream_id[0] ? s_input_stream_id : "audio", "sensor.mic",
                                     NULL, 0, s_user_id, s_session_id,
                                     true, metadata,
                                     s_final_buf, sizeof(s_final_buf), &written) != 0) {
        return ESP_FAIL;
    }

    int ret = esp_websocket_client_send_bin(s_stream_client, (const char *)s_final_buf, written,
                                             1000 / portTICK_PERIOD_MS);
    return ret >= 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t ws_stream_send_image(const uint8_t *data, size_t len) {
    if (!s_stream_client || !esp_websocket_client_is_connected(s_stream_client)) {
        return ESP_FAIL;
    }

    uint8_t encoded[65536];
    size_t written = 0;

    if (audio_chat_stream_encode("camera", "sensor.rgb", data, len,
                                  s_user_id, s_session_id,
                                  encoded, sizeof(encoded), &written) != 0) {
        return ESP_FAIL;
    }

    int ret = esp_websocket_client_send_bin(s_stream_client, (const char *)encoded, written,
                                             1000 / portTICK_PERIOD_MS);
    return ret >= 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t ws_stream_send_sensor_data(const char *json_data, size_t len) {
    if (!s_stream_client || !esp_websocket_client_is_connected(s_stream_client)) {
        return ESP_FAIL;
    }
    int ret = esp_websocket_client_send_text(s_stream_client, json_data, len,
                                             1000 / portTICK_PERIOD_MS);
    return ret >= 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t ws_stream_send_imu(const char *json_data, size_t len) {
    if (!s_stream_client || !esp_websocket_client_is_connected(s_stream_client)) {
        return ESP_FAIL;
    }

    uint8_t encoded[1024];
    size_t written = 0;

    if (audio_chat_stream_encode_imu(s_user_id, s_session_id,
                                      json_data, len,
                                      encoded, sizeof(encoded), &written) != 0) {
        return ESP_FAIL;
    }

    int ret = esp_websocket_client_send_bin(s_stream_client, (const char *)encoded, written,
                                             1000 / portTICK_PERIOD_MS);
    return ret >= 0 ? ESP_OK : ESP_FAIL;
}

bool ws_stream_is_connected(void) {
    return s_stream_connected;
}

esp_err_t ws_stream_disconnect(void) {
    s_initialized = false;

    if (s_reconnect_task) {
        vTaskDelete(s_reconnect_task);
        s_reconnect_task = NULL;
    }

    if (s_stream_client) {
        esp_websocket_client_stop(s_stream_client);
        esp_websocket_client_destroy(s_stream_client);
        s_stream_client = NULL;
        s_stream_connected = false;
    }
    return ESP_OK;
}