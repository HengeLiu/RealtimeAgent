#include <string.h>
#include <stdlib.h>

#include "adapters/ra_esp32_camera.h"
#include "adapters/ra_esp32_mic.h"
#include "adapters/ra_esp32_speaker.h"
#include "adapters/ra_esp32_transport.h"
#include "adapters/ra_esp32_wake_word.h"
#include "board/board_config.h"
#include "diagnostics/ra_esp32_diag.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "realtime_agent_device/ra_client.h"
#include "sdkconfig.h"

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT BIT1
#define CONTROL_RECV_BUFFER_SIZE 4096
#define OUTPUT_RECV_BUFFER_SIZE 32768
#define CONTROL_HEARTBEAT_INTERVAL_MS 10000

static const char *TAG = "ra_esp32_app";
static EventGroupHandle_t s_wifi_event_group;
static int s_wifi_retry_count = 0;
static ra_device_client_t *s_client = NULL;
static const char *s_device_properties =
    "{\"audio.aec\":\"disabled\","
    "\"audio.playback_reference\":\"not_wired\","
    "\"audio.wake_word\":\"disabled\","
    "\"audio.full_duplex\":true,"
    "\"audio.mic_policy\":\"keep_uploading_raw_pdm\"}";
static ra_transport_t s_sdk_transport;
static ra_mic_source_t s_mic_source;
static ra_speaker_sink_t s_speaker_sink;
static ra_camera_source_t s_camera_source;
static SemaphoreHandle_t s_speaker_buffer_mutex = NULL;

#if CONFIG_REALTIME_AGENT_AUTO_WAKE_SMOKE
static bool s_auto_wake_sent = false;

static void auto_wake_smoke_task(void *arg) {
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(1000));
    int rc = ra_device_client_start_conversation(s_client, "esp32_auto_wake_smoke");
    if (rc == RA_OK) {
        ESP_LOGI(TAG, "auto_wake_smoke.detected sent");
    } else {
        ESP_LOGW(TAG, "auto_wake_smoke.detected failed rc=%d", rc);
    }
    vTaskDelete(NULL);
}
#endif

static void maybe_start_auto_wake_smoke(void) {
#if CONFIG_REALTIME_AGENT_AUTO_WAKE_SMOKE
    bool should_start = !s_auto_wake_sent && ra_device_client_connection_state(s_client) == RA_CLIENT_REGISTERED;
    if (should_start) {
        s_auto_wake_sent = true;
    }
    if (should_start) {
        if (xTaskCreate(auto_wake_smoke_task, "ra_auto_wake", 4096, NULL, 4, NULL) != pdPASS) {
            ESP_LOGW(TAG, "auto_wake_smoke task create failed");
        }
    }
#endif
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data) {
    (void)arg;
    (void)event_data;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_wifi_retry_count < 10) {
            esp_wifi_connect();
            s_wifi_retry_count++;
            ESP_LOGW(TAG, "wifi.retry count=%d", s_wifi_retry_count);
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "wifi.connected ip=" IPSTR, IP2STR(&event->ip_info.ip));
        s_wifi_retry_count = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static esp_err_t wifi_connect(void) {
    if (strlen(CONFIG_REALTIME_AGENT_WIFI_SSID) == 0) {
        ESP_LOGE(TAG, "CONFIG_REALTIME_AGENT_WIFI_SSID is empty");
        return ESP_FAIL;
    }

    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_config));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));

    wifi_config_t wifi_config = {0};
    strlcpy((char *)wifi_config.sta.ssid, CONFIG_REALTIME_AGENT_WIFI_SSID, sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password, CONFIG_REALTIME_AGENT_WIFI_PASSWORD, sizeof(wifi_config.sta.password));
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group,
        WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(30000)
    );
    if (bits & WIFI_CONNECTED_BIT) {
        return ESP_OK;
    }
    ESP_LOGE(TAG, "wifi.connect failed");
    return ESP_FAIL;
}

static void control_receive_task(void *arg) {
    (void)arg;
    char *text = (char *)malloc(CONTROL_RECV_BUFFER_SIZE);
    if (text == NULL) {
        ESP_LOGE(TAG, "control receive buffer alloc failed");
        vTaskDelete(NULL);
        return;
    }
    while (true) {
        size_t size = 0;
        int rc = s_sdk_transport.recv_text(s_sdk_transport.ctx, RA_TRANSPORT_CONTROL, text, CONTROL_RECV_BUFFER_SIZE, &size);
        if (rc != 0) {
            ESP_LOGW(TAG, "control.recv failed");
            (void)ra_device_client_handle_transport_disconnected(s_client, RA_TRANSPORT_CONTROL);
            vTaskDelay(pdMS_TO_TICKS(1000));
            rc = ra_device_client_start(s_client);
            if (rc != RA_OK) {
                ESP_LOGW(TAG, "client.reconnect failed rc=%d", rc);
                vTaskDelay(pdMS_TO_TICKS(2000));
            } else {
                ESP_LOGI(TAG, "client.reconnect requested");
            }
            continue;
        }
        ESP_LOGI(TAG, "control.event bytes=%u", (unsigned)size);
        rc = ra_device_client_handle_event(s_client, text);
        if (rc != RA_OK) {
            ESP_LOGW(TAG, "control.event handle failed rc=%d text=%s", rc, text);
        }
        maybe_start_auto_wake_smoke();
        ra_diagnostics_t diagnostics;
        ra_device_client_get_diagnostics(s_client, &diagnostics);
        ra_esp32_diag_log_snapshot(&diagnostics);
    }
}

static void mic_upload_task(void *arg) {
    (void)arg;
    size_t failure_count = 0;
    while (true) {
        int rc = ra_device_client_send_mic_chunk(s_client);
        if (rc == RA_ERROR_STATE) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        if (rc != RA_OK) {
            failure_count++;
            if (failure_count % 50 == 1) {
                ESP_LOGW(TAG, "mic.chunk send failed rc=%d failures=%u", rc, (unsigned)failure_count);
            }
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        failure_count = 0;
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

static void audio_output_receive_task(void *arg) {
    (void)arg;
    uint8_t *data = malloc(OUTPUT_RECV_BUFFER_SIZE);
    if (data == NULL) {
        ESP_LOGE(TAG, "audio_output buffer alloc failed");
        vTaskDelete(NULL);
        return;
    }
    size_t chunk_count = 0;
    size_t total_bytes = 0;
    while (true) {
        size_t size = 0;
        int rc = s_sdk_transport.recv_binary(s_sdk_transport.ctx, RA_TRANSPORT_AUDIO_OUTPUT, data, OUTPUT_RECV_BUFFER_SIZE, &size);
        if (rc != 0) {
            ESP_LOGW(TAG, "speaker.chunk recv failed");
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        chunk_count++;
        total_bytes += size;
        if (chunk_count <= 3 || chunk_count % 20 == 0) {
            ESP_LOGI(TAG, "speaker.chunk received bytes=%u chunks=%u total=%u",
                     (unsigned)size, (unsigned)chunk_count, (unsigned)total_bytes);
        }
        rc = ra_device_client_handle_output_chunk(s_client, data, size);
        if (rc != RA_OK) {
            ESP_LOGW(TAG, "speaker.chunk handle failed rc=%d bytes=%u", rc, (unsigned)size);
        }
    }
}

static void audio_output_playback_task(void *arg) {
    (void)arg;
    while (true) {
        int rc = ra_device_client_pump_output(s_client);
        if (rc == RA_ERROR_STATE) {
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }
        if (rc != RA_OK) {
            ESP_LOGW(TAG, "speaker.playback pump failed rc=%d", rc);
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

static void heartbeat_task(void *arg) {
    (void)arg;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(CONTROL_HEARTBEAT_INTERVAL_MS));
        int rc = ra_device_client_send_heartbeat(s_client);
        if (rc == RA_ERROR_STATE) {
            continue;
        }
        if (rc != RA_OK) {
            ESP_LOGW(TAG, "heartbeat.send failed rc=%d", rc);
        }
    }
}

static void connection_state_changed(ra_client_connection_state_t state, void *user_data) {
    (void)user_data;
    ESP_LOGI(TAG, "connection.state=%s", ra_client_connection_state_name(state));
}

static void *speaker_buffer_alloc(void *ctx, size_t size) {
    (void)ctx;
    void *ptr = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (ptr == NULL) {
        ptr = heap_caps_malloc(size, MALLOC_CAP_8BIT);
    }
    return ptr;
}

static void speaker_buffer_free(void *ctx, void *ptr) {
    (void)ctx;
    heap_caps_free(ptr);
}

static void speaker_buffer_lock(void *ctx) {
    SemaphoreHandle_t mutex = (SemaphoreHandle_t)ctx;
    if (mutex != NULL) {
        xSemaphoreTake(mutex, portMAX_DELAY);
    }
}

static void speaker_buffer_unlock(void *ctx) {
    SemaphoreHandle_t mutex = (SemaphoreHandle_t)ctx;
    if (mutex != NULL) {
        xSemaphoreGive(mutex);
    }
}

static void client_start_task(void *arg) {
    (void)arg;
    int rc = ra_device_client_start(s_client);
    if (rc != RA_OK) {
        ESP_LOGE(TAG, "client.start failed rc=%d", rc);
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "client.register.requested server=%s device_id=%s", CONFIG_REALTIME_AGENT_SERVER_URL, CONFIG_REALTIME_AGENT_DEVICE_ID);

    xTaskCreate(control_receive_task, "ra_control_rx", 8192, NULL, 5, NULL);
    xTaskCreate(mic_upload_task, "ra_mic_upload", 8192, NULL, 5, NULL);
    xTaskCreate(audio_output_receive_task, "ra_audio_output_rx", 8192, NULL, 5, NULL);
    xTaskCreate(audio_output_playback_task, "ra_audio_playback", 8192, NULL, 5, NULL);
    xTaskCreate(heartbeat_task, "ra_heartbeat", 4096, NULL, 4, NULL);
    ra_esp32_wake_word_start(s_client);
    vTaskDelete(NULL);
}

void app_main(void) {
    ESP_LOGI(TAG, "realtime-agent esp32-s3 device demo boot");
    esp_err_t nvs_result = nvs_flash_init();
    if (nvs_result == ESP_ERR_NVS_NO_FREE_PAGES || nvs_result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_result = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_result);

    if (wifi_connect() != ESP_OK) {
        ESP_LOGE(TAG, "stop because wifi is not connected");
        return;
    }

    const esp32s3_board_config_t *board = esp32s3_board_default_config();
    ra_esp32_transport_t *esp_transport = ra_esp32_transport_create(CONFIG_REALTIME_AGENT_SERVER_URL, CONFIG_REALTIME_AGENT_DEVICE_ID);
    ra_esp32_mic_t *esp_mic = ra_esp32_mic_create(&board->mic);
    ra_esp32_speaker_t *esp_speaker = ra_esp32_speaker_create(&board->speaker);
    ra_esp32_camera_t *esp_camera = ra_esp32_camera_create(&board->camera);
    if (esp_transport == NULL || esp_mic == NULL || esp_speaker == NULL || esp_camera == NULL) {
        ESP_LOGE(TAG, "adapter.create failed");
        return;
    }

    s_sdk_transport = ra_esp32_transport_as_sdk_transport(esp_transport);
    s_mic_source = ra_esp32_mic_as_source(esp_mic);
    s_speaker_sink = ra_esp32_speaker_as_sink(esp_speaker);
    s_camera_source = ra_esp32_camera_as_source(esp_camera);

    ra_speaker_buffer_config_t speaker_buffer = ra_speaker_buffer_default_config();
    speaker_buffer.start_watermark_ms = 240;
    speaker_buffer.low_watermark_ms = 1200;
    speaker_buffer.high_watermark_ms = 8000;
    speaker_buffer.max_buffer_ms = 12000;
    speaker_buffer.max_payload_bytes = 2048;
    speaker_buffer.max_chunks = 768;
    s_speaker_buffer_mutex = xSemaphoreCreateMutex();
    if (s_speaker_buffer_mutex == NULL) {
        ESP_LOGE(TAG, "speaker_buffer mutex create failed");
        return;
    }
    speaker_buffer.alloc = speaker_buffer_alloc;
    speaker_buffer.free = speaker_buffer_free;
    speaker_buffer.lock_ctx = s_speaker_buffer_mutex;
    speaker_buffer.lock = speaker_buffer_lock;
    speaker_buffer.unlock = speaker_buffer_unlock;

    ra_device_client_config_t config = {
        .server_url = CONFIG_REALTIME_AGENT_SERVER_URL,
        .device_id = CONFIG_REALTIME_AGENT_DEVICE_ID,
        .user_id = CONFIG_REALTIME_AGENT_USER_ID,
        .name = CONFIG_REALTIME_AGENT_DEVICE_NAME,
        .client_type = "esp32-s3",
        .properties_json = s_device_properties,
        .mic = &s_mic_source,
        .speaker = &s_speaker_sink,
        .camera = &s_camera_source,
        .transport = &s_sdk_transport,
        .speaker_buffer = speaker_buffer,
        .log_level = RA_LOG_INFO,
    };
    s_client = ra_device_client_create(&config);
    if (s_client == NULL) {
        ESP_LOGE(TAG, "client.create failed");
        return;
    }
    ra_device_client_on_connection_state_change(s_client, connection_state_changed, NULL);

    if (xTaskCreate(client_start_task, "ra_client_start", 16384, NULL, 5, NULL) != pdPASS) {
        ESP_LOGE(TAG, "client_start_task create failed");
    }
}
