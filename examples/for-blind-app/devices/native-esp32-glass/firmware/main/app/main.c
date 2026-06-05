#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"
#include "esp_random.h"
#include "esp_log.h"
#include "nvs_flash.h"

#include "connectivity/wifi_manager.h"
#include "connectivity/ws_control.h"
#include "connectivity/ws_stream.h"
#include "drivers/camera.h"
#include "drivers/audio.h"
#include "drivers/imu.h"
#include "drivers/feedback_tone.h"
#include "storage/config_store.h"
#include "provisioning/provisioning.h"
#include "protocol/protocol_adapter.h"
#include "app/device.h"
#include "utils/wake_word.h"

static const char *TAG = "main";

// Runtime config from NVS
static device_config_t g_config;

// State
static audio_chat_device_t g_device;
static char s_server_url[128];
static bool s_registered = false;
static bool s_session_active = false;
static bool s_streaming = false;
static bool s_start_streaming_requested = false;
static uint32_t s_session_counter = 0;
static char s_current_stream_id[128] = {0};

// Callbacks
static void on_wifi_connected(void) { ESP_LOGI(TAG, "WiFi connected"); }
static void on_wifi_disconnected(void) { ESP_LOGI(TAG, "WiFi disconnected"); }
static void on_control_connected(void) { ESP_LOGI(TAG, "Control WS connected"); }
static void on_control_disconnected(void) {
    ESP_LOGI(TAG, "Control WS disconnected");
    s_registered = false;
    // Control WS 断开时，stream WS 也必须断开，因为服务器端 stream 注册已失效
    if (s_streaming) {
        audio_stop_streaming();
        ws_stream_disconnect();
        s_session_active = false;
        s_streaming = false;
        s_current_stream_id[0] = '\0';
        ESP_LOGI(TAG, "Stream stopped due to control WS disconnect");
    }
}

static void on_speaker_drain_complete(void) {
    ESP_LOGI(TAG, "Speaker drain complete - sending close acks");
    const char *finished = "{\"stream_type\":\"actuator.speaker\"}";
    ws_control_send_event("stream.output.finished", finished, strlen(finished));
    ws_control_send_event("stream.output.closed", finished, strlen(finished));
}

static void on_wake_word_detected(void) {
    ESP_LOGI(TAG, "Wake word detected");
    if (s_registered && ws_control_is_connected()) {
        const char *wake_payload = "{\"wake_source\":\"esp32_wake_word\"}";
        ws_control_send_event("control.user.wake.detected",
            wake_payload, strlen(wake_payload));
    }
}

static void on_control_message(const char *event_name, const char *payload, size_t len) {
    ESP_LOGI(TAG, "CTRL EVENT: %s (payload %d bytes)", event_name, (int)len);

    if (strcmp(event_name, "control.device.registered") == 0) {
        s_registered = true;
        ESP_LOGI(TAG, "Registered OK");
    }
    else if (strcmp(event_name, "control.audio_session.open.requested") == 0) {
        ESP_LOGI(TAG, "Audio session open requested - deferring to main loop");
        // Set flag for main loop to handle (avoid blocking WS callback)
        s_start_streaming_requested = true;
    }
    else if (strcmp(event_name, "control.audio_session.close.requested") == 0) {
        // Stop speaker and streaming
        audio_speaker_stop();
        audio_stop_streaming();
        // Send final chunk + stream.input.closed before disconnecting
        if (s_current_stream_id[0]) {
            ws_stream_send_final("audio_segment_closed");
            char closed_buf[128];
            int cn = snprintf(closed_buf, sizeof(closed_buf),
                "{\"stream_type\":\"sensor.mic\",\"reason\":\"audio_segment_closed\"}");
            ws_control_send_event_with_stream("stream.input.closed", closed_buf, cn,
                s_current_stream_id, "sensor.mic");
        }
        // camera_task_stop();
        ws_stream_disconnect();
        const char *closed_payload = "{\"reason\":\"esp32_device_closed\"}";
        ws_control_send_event("control.audio_session.closed",
            closed_payload, strlen(closed_payload));
        s_session_active = false;
        s_streaming = false;
        s_current_stream_id[0] = '\0';
        ESP_LOGI(TAG, "Audio session stopped");
    }
    else if (strcmp(event_name, "stream.output.open.requested") == 0) {
        ESP_LOGI(TAG, ">>> stream.output.open.requested RECEIVED - starting speaker");
        esp_err_t spk_ret = audio_speaker_start();
        ESP_LOGI(TAG, "audio_speaker_start returned: %d", spk_ret);
        const char *started = "{\"stream_type\":\"actuator.speaker\"}";
        ws_control_send_event("stream.output.started", started, strlen(started));
    }
    else if (strcmp(event_name, "stream.output.close.requested") == 0) {
        ESP_LOGI(TAG, ">>> stream.output.close.requested RECEIVED - draining speaker (deferring close ack)");
        audio_speaker_drain_stop();
        // Do NOT send finished/closed here — let the speaker playback task
        // send them after all buffered audio has played (like browser demo).
        // This keeps the stream WS open for continued mic upload.
    }
    else if (strcmp(event_name, "control.user.interrupt.detected") == 0) {
        audio_stop_streaming();
        ESP_LOGI(TAG, "Interrupt detected");
    }
    else if (strcmp(event_name, "stream.control.open.requested") == 0) {
        // camera_task_start();  // DISABLED: conflicts with PDM mic
        ESP_LOGI(TAG, "Camera disabled (PDM mic priority)");
    }
    else if (strcmp(event_name, "stream.control.close.requested") == 0) {
        // camera_task_stop();
        ESP_LOGI(TAG, "Camera disabled");
    }
    else if (strcmp(event_name, "system.error.raised") == 0) {
        ESP_LOGW(TAG, "Server error: %.*s", (int)(len > 200 ? 200 : len), payload);
    }
    else {
        ESP_LOGI(TAG, "Unhandled event: %s (payload %d bytes)", event_name, (int)len);
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "=== ESP32-S3 Glass Firmware ===");

    // NVS + Config
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    esp_log_level_set("*", ESP_LOG_INFO);

    config_store_init();
    memset(&g_config, 0, sizeof(g_config));

    // Random session counter to avoid stale stream_id from previous boot
    s_session_counter = esp_random();
    ESP_LOGI(TAG, "Session counter init: %lu", (unsigned long)s_session_counter);

    // Load config from NVS
    bool need_provisioning = false;
    if (config_store_load(&g_config) != ESP_OK || !g_config.configured) {
        need_provisioning = true;
    }

    // Generate hw_id if not set
    if (g_config.hw_id[0] == '\0') {
        config_store_generate_hw_id(g_config.hw_id, sizeof(g_config.hw_id));
    }

    if (need_provisioning) {
        // ===== Provisioning Mode (WiFi AP + captive portal) =====
        ESP_LOGI(TAG, "No config found, entering provisioning mode");
        ESP_LOGI(TAG, "Connect to WiFi AP 'Glass-XXXX' to provision this device");

        // Start provisioning (WiFi AP + captive portal + DNS)
        provisioning_start(&g_config);

        // Wait for provisioning to complete
        while (provisioning_get_state() != PROV_STATE_DONE &&
               provisioning_get_state() != PROV_STATE_ERROR) {
            vTaskDelay(pdMS_TO_TICKS(500));
        }

        if (provisioning_get_state() == PROV_STATE_DONE) {
            ESP_LOGI(TAG, "Provisioning successful, restarting...");
            vTaskDelay(pdMS_TO_TICKS(1000));
            esp_restart();
        } else {
            ESP_LOGE(TAG, "Provisioning failed");
            vTaskDelay(pdMS_TO_TICKS(3000));
            esp_restart();  // Retry provisioning on next boot
        }
        return;
    }

    // ===== Normal Mode =====
    ESP_LOGI(TAG, "Config loaded: device=%s user=%s server=%s:%d hw=%s",
             g_config.device_id, g_config.user_id,
             g_config.server_host, g_config.server_port, g_config.hw_id);

    // Device model (before WiFi, matching original order)
    audio_chat_device_init(&g_device, g_config.user_id, g_config.device_id);
    audio_chat_device_set_name(&g_device, "ESP32-S3 Glass");
    audio_chat_device_set_role(&g_device, "glass");
    audio_chat_device_add_rgb_sensor(&g_device);
    audio_chat_device_add_imu_sensor(&g_device);
    audio_chat_device_add_vibrator(&g_device);
    if (g_config.auth_token[0] != '\0') {
        audio_chat_device_set_auth(&g_device, "token", g_config.auth_token);
    }

    // Server URL
    snprintf(s_server_url, sizeof(s_server_url), "ws://%s:%d/ws/control",
             g_config.server_host, g_config.server_port);

    // WiFi fail count: auto-reset after 5 consecutive failures
    if (g_config.wifi_fail_count >= 5) {
        ESP_LOGW(TAG, "WiFi failed %d times, clearing config", g_config.wifi_fail_count);
        config_store_clear_all();
        esp_restart();
    }

    // WiFi
    config_store_increment_fail_count(NULL);
    ret = wifi_manager_init(g_config.wifi_ssid, g_config.wifi_pass);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WiFi init failed");
        vTaskDelay(pdMS_TO_TICKS(2000));
        esp_restart();
    }
    wifi_manager_set_callbacks(on_wifi_connected, on_wifi_disconnected);
    int w = 0;
    while (!wifi_manager_is_connected() && w++ < 30) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    if (!wifi_manager_is_connected()) {
        ESP_LOGE(TAG, "WiFi timeout");
        vTaskDelay(pdMS_TO_TICKS(2000));
        esp_restart();
    }
    config_store_reset_fail_count();
    ESP_LOGI(TAG, "WiFi OK: %s", wifi_manager_get_local_ip());

    // Peripherals
    // camera_init();  // DISABLED: conflicts with PDM mic on I2S controller 0

    // Audio init AFTER WiFi (matching verified working order from 229ec06)
    ESP_LOGI(TAG, "Initializing audio...");
    esp_err_t audio_ret = audio_init();
    ESP_LOGI(TAG, "audio_init returned: %d", audio_ret);
    tone_play_wifi_connected();

    ESP_LOGI(TAG, "Initializing wake word...");
    esp_err_t ww_ret = wake_word_init();
    ESP_LOGI(TAG, "wake_word_init returned: %d", ww_ret);
    if (ww_ret == ESP_OK) {
        wake_word_set_callback(on_wake_word_detected);
        ESP_LOGI(TAG, "Wake word callback set");
        ww_ret = wake_word_start();
        ESP_LOGI(TAG, "wake_word_start returned: %d", ww_ret);
        ESP_LOGI(TAG, "Starting wake word audio capture...");
        ww_ret = audio_start_wake_word_detection();
        ESP_LOGI(TAG, "audio_start_wake_word_detection returned: %d", ww_ret);
        if (ww_ret == ESP_OK) {
            ESP_LOGI(TAG, "=== Wake word system READY ===");
        } else {
            ESP_LOGE(TAG, "Wake word audio capture FAILED: %d", ww_ret);
        }
    } else {
        ESP_LOGE(TAG, "Wake word init FAILED: %d (model partition missing?)", ww_ret);
    }
    ESP_LOGI(TAG, "Initializing IMU...");
    imu_init();

    // Control WebSocket
    ret = ws_control_init(s_server_url, g_config.device_id, &g_device);
    if (ret == ESP_OK) {
        ws_control_set_callbacks(on_control_connected, on_control_disconnected, on_control_message);
        ws_control_task_start();
        ESP_LOGI(TAG, "Control WS started");
    }

    audio_speaker_set_drain_callback(on_speaker_drain_complete);

    ESP_LOGI(TAG, "=== Ready, waiting for wake word ===");

    // Main loop - heartbeat every 10s, connection check every 1s
    int hb_counter = 0;
    int reconnect_cooldown = 0;
    int ws_fail_count = 0;
    const int WS_MAX_FAILS = 12;  // ~60 seconds of failures (5s cooldown × 12)

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(1000));

        // Check WiFi
        if (!wifi_manager_is_connected()) {
            ESP_LOGW(TAG, "WiFi lost");
            continue;
        }

        // Check WebSocket connection
        if (!ws_control_is_connected()) {
            if (reconnect_cooldown <= 0) {
                ws_fail_count++;
                ESP_LOGW(TAG, "WS lost, reconnecting... (fail %d/%d)", ws_fail_count, WS_MAX_FAILS);
                s_registered = false;
                ws_control_reconnect();
                reconnect_cooldown = 5;  // Wait 5 seconds before next reconnect

                if (ws_fail_count >= WS_MAX_FAILS) {
                    ESP_LOGE(TAG, "WS failed %d times, clearing config and re-provisioning...", ws_fail_count);
                    config_store_clear_all();
                    vTaskDelay(pdMS_TO_TICKS(1000));
                    esp_restart();
                }
            } else {
                reconnect_cooldown--;
            }
            continue;
        }

        // Reset fail count when connected
        ws_fail_count = 0;

        // Reset cooldown when connected
        reconnect_cooldown = 0;

        // Handle deferred streaming start
        if (s_start_streaming_requested) {
            s_start_streaming_requested = false;
            ESP_LOGI(TAG, "Processing deferred streaming start");

            // Generate unique stream_id per session (like browser demo's newId("stream_in"))
            s_session_counter++;
            snprintf(s_current_stream_id, sizeof(s_current_stream_id), "stream_in_%s_%lu", g_config.device_id, (unsigned long)s_session_counter);

            // Step 1: Send audio session opened
            const char *opened_payload = "{\"reason\":\"esp32_device_opened\"}";
            ws_control_send_event("control.audio_session.opened",
                opened_payload, strlen(opened_payload));

            // Step 2: Send stream input opened BEFORE opening stream WS
            char buf[256];
            int n = snprintf(buf, sizeof(buf),
                "{\"stream_type\":\"sensor.mic\","
                "\"format\":{\"codec\":\"pcm16le\",\"sample_rate\":16000,\"channels\":1,\"chunk_ms\":20}}");
            ws_control_send_event_with_stream("stream.input.opened", buf, n, s_current_stream_id, "sensor.mic");

            // Step 3: Wait for server to process stream.input.opened
            vTaskDelay(pdMS_TO_TICKS(1000));

            // Step 4: Now open stream WebSocket
            char stream_url[128];
            snprintf(stream_url, sizeof(stream_url), "ws://%s:%d/ws/stream", g_config.server_host, g_config.server_port);
            esp_err_t stream_ret = ws_stream_init(stream_url, g_config.device_id, s_current_stream_id);
            ESP_LOGI(TAG, "ws_stream_init returned: %d", stream_ret);

            if (stream_ret == ESP_OK) {
                // Step 5: Wait for stream WS to actually connect (poll up to 5s)
                bool ws_ready = false;
                for (int i = 0; i < 50; i++) {
                    if (ws_stream_is_connected()) {
                        ws_ready = true;
                        break;
                    }
                    vTaskDelay(pdMS_TO_TICKS(100));
                }
                if (ws_ready) {
                    // Step 6: Start audio streaming
                    esp_err_t audio_ret = audio_start_streaming();
                    ESP_LOGI(TAG, "audio_start_streaming returned: %d", audio_ret);
                    s_session_active = true;
                    s_streaming = true;
                    ESP_LOGI(TAG, "Audio session started (stream_id=%s)", s_current_stream_id);
                } else {
                    ESP_LOGE(TAG, "Stream WS failed to connect within 5s");
                    ws_stream_disconnect();
                    // Clean up server-side session
                    char closed_buf[128];
                    int cn = snprintf(closed_buf, sizeof(closed_buf),
                        "{\"stream_type\":\"sensor.mic\",\"reason\":\"stream_ws_timeout\"}");
                    ws_control_send_event_with_stream("stream.input.closed", closed_buf, cn,
                        s_current_stream_id, "sensor.mic");
                    const char *closed_payload = "{\"reason\":\"stream_ws_timeout\"}";
                    ws_control_send_event("control.audio_session.closed",
                        closed_payload, strlen(closed_payload));
                    s_current_stream_id[0] = '\0';
                }
            } else {
                ESP_LOGE(TAG, "Failed to init stream WebSocket");
                // Clean up server-side session
                char closed_buf[128];
                int cn = snprintf(closed_buf, sizeof(closed_buf),
                    "{\"stream_type\":\"sensor.mic\",\"reason\":\"stream_ws_failed\"}");
                ws_control_send_event_with_stream("stream.input.closed", closed_buf, cn,
                    s_current_stream_id, "sensor.mic");
                const char *closed_payload = "{\"reason\":\"stream_ws_failed\"}";
                ws_control_send_event("control.audio_session.closed",
                    closed_payload, strlen(closed_payload));
                s_current_stream_id[0] = '\0';
            }
        }

        // Send heartbeat every 10 seconds
        if (s_registered) {
            hb_counter++;
            if (hb_counter >= 10) {
                hb_counter = 0;
                ESP_LOGI(TAG, "SEND HB");
                const char *hb_payload = "{\"connection_state\":\"online\",\"client_type\":\"esp32-glass\"}";
                ws_control_send_event("control.device.heartbeat.received",
                    hb_payload, strlen(hb_payload));
            }
        }
    }
}
