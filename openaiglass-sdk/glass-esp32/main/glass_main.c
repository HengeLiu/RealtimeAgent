#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "driver/i2s_pdm.h"
#include "driver/i2s_std.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "esp_camera.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_psram.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/idf_additions.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "nvs_flash.h"

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAILED_BIT BIT1
#define WIFI_PROFILE_COUNT 2
#define WIFI_RETRY_DELAY_MS 5000
#define CONTROL_TARGET_DEVICE_ID "server-main"

#define MIC_PDM_CLK_GPIO GPIO_NUM_42
#define MIC_PDM_DATA_GPIO GPIO_NUM_41
#define SPK_I2S_BCLK_GPIO GPIO_NUM_7
#define SPK_I2S_LRCK_GPIO GPIO_NUM_8
#define SPK_I2S_DOUT_GPIO GPIO_NUM_9
#define CAM_XCLK_GPIO GPIO_NUM_10
#define CAM_SIOD_GPIO GPIO_NUM_40
#define CAM_SIOC_GPIO GPIO_NUM_39
#define CAM_Y9_GPIO GPIO_NUM_48
#define CAM_Y8_GPIO GPIO_NUM_11
#define CAM_Y7_GPIO GPIO_NUM_12
#define CAM_Y6_GPIO GPIO_NUM_14
#define CAM_Y5_GPIO GPIO_NUM_16
#define CAM_Y4_GPIO GPIO_NUM_18
#define CAM_Y3_GPIO GPIO_NUM_17
#define CAM_Y2_GPIO GPIO_NUM_15
#define CAM_VSYNC_GPIO GPIO_NUM_38
#define CAM_HREF_GPIO GPIO_NUM_47
#define CAM_PCLK_GPIO GPIO_NUM_13
#define CAM_PWDN_GPIO (-1)
#define CAM_RESET_GPIO (-1)
#define SR_SAMPLE_RATE_HZ 16000
#ifndef CONFIG_GLASS_WAKE_PROMPT_TONE_ENABLE
#define CONFIG_GLASS_WAKE_PROMPT_TONE_ENABLE 0
#endif
#ifndef CONFIG_GLASS_WAKE_PROMPT_TONE_DURATION_MS
#define CONFIG_GLASS_WAKE_PROMPT_TONE_DURATION_MS 70
#endif
#ifndef CONFIG_GLASS_WAKE_PROMPT_TONE_FREQ_HZ
#define CONFIG_GLASS_WAKE_PROMPT_TONE_FREQ_HZ 1760
#endif
#ifndef CONFIG_GLASS_WAKE_PROMPT_TONE_GAIN_PERMILLE
#define CONFIG_GLASS_WAKE_PROMPT_TONE_GAIN_PERMILLE 120
#endif
#define AFE_INPUT_FORMAT "M"
#define LOCAL_ENDPOINT_TAIL_MS 900
#define LOCAL_ENDPOINT_MIN_MS 1200
#define LOCAL_ENDPOINT_MAX_MS 8000
#define AUDIO_FRAME_SAMPLES 320
#define AUDIO_FRAME_BYTES (AUDIO_FRAME_SAMPLES * sizeof(int16_t))
#define PRE_ROLL_FRAME_COUNT 8
#define WAKE_IDLE_SUMMARY_MS 3000
#define SERVER_REPLY_TIMEOUT_MS 45000
#define CONTINUOUS_DIALOG_IDLE_TIMEOUT_MS 30000
#define AUDIO_WS_RECONNECT_INTERVAL_MS 3000
#define PLAYBACK_HTTP_TIMEOUT_MS 5000
#define PLAYBACK_STREAM_IDLE_TIMEOUT_MS 30000
#define CAMERA_FRAME_SIZE FRAMESIZE_VGA
#define CAMERA_JPEG_QUALITY 18
#define CAMERA_FB_COUNT 1
#define CAMERA_CAPTURE_TASK_STACK_SIZE (6 * 1024)
#define CAMERA_STREAM_TASK_STACK_SIZE (8 * 1024)
#define WAKE_PROMPT_TONE_TABLE_SIZE 32

typedef struct {
    const char *ssid;
    const char *password;
} wifi_profile_t;

typedef struct {
    const char *server_ws_uri;
    const char *device_id;
    const char *pair_token;
    const char *firmware_version;
    uint32_t heartbeat_interval_ms;
} glass_runtime_config_t;

typedef struct {
    esp_afe_sr_iface_t *afe_handle;
    esp_afe_sr_data_t *afe_data;
    int16_t *feed_buffer;
    size_t feed_buffer_size_bytes;
    int feed_chunksize;
    int feed_nch;
    int feed_chunk_ms;
    bool initialized;
    char wake_model_name[64];
} sr_runtime_ctx_t;

typedef struct {
    bool segment_active;
    bool got_speech;
    int tail_silence_ms;
    int elapsed_ms;
    uint32_t segment_pcm_bytes;
    uint32_t chunk_seq;
    char segment_id[64];
} segment_state_t;

typedef struct {
    uint8_t data[AUDIO_FRAME_BYTES];
    size_t size;
    bool valid;
} pre_roll_frame_t;

static const char *TAG = "glass-main";
static EventGroupHandle_t s_wifi_event_group;
static esp_websocket_client_handle_t s_ws_client;
static esp_websocket_client_handle_t s_audio_ws_client;
static esp_websocket_client_handle_t s_camera_ws_client;
static esp_event_handler_instance_t s_wifi_event_instance;
static esp_event_handler_instance_t s_ip_event_instance;
static i2s_chan_handle_t s_mic_rx_chan;
static i2s_chan_handle_t s_spk_tx_chan;
static int s_active_wifi_profile = 0;
static int s_wifi_round_attempt_count = 0;
static uint32_t s_message_sequence = 0;
static bool s_registered = false;
static bool s_voice_session_opened = false;
static bool s_wake_listening_enabled = false;
static bool s_sr_task_started = false;
static bool s_audio_ws_ready = false;
static bool s_audio_transport_started = false;
static bool s_control_transport_started = false;
static bool s_playback_active = false;
static bool s_playback_task_running = false;
static volatile bool s_playback_interrupt_requested = false;
static bool s_realtime_semantic_dialog_enabled = false;
static bool s_continuous_dialog_active = false;
static bool s_speaker_channel_enabled = false;
static bool s_camera_initialized = false;
static bool s_camera_capture_busy = false;
static bool s_camera_stream_active = false;
static bool s_camera_stream_task_running = false;
static uint64_t s_reply_wait_started_ms = 0;
static char s_current_session_id[64];
static char s_current_stream_id[64];
static char s_current_playback_stream_id[64];
static char s_next_playback_stream_id[64];
static char s_audio_ws_uri[256];
static char s_stream_wav_url[256];
static char s_camera_stream_ws_uri[256];
static char s_current_camera_stream_id[64];
static int s_camera_frame_interval_ms = 500;
static uint32_t s_camera_frame_seq = 0;
static uint64_t s_playback_request_started_ms = 0;
static uint64_t s_continuous_dialog_last_activity_ms = 0;
static TaskHandle_t s_playback_task_handle = NULL;
static TaskHandle_t s_camera_stream_task_handle = NULL;
static TaskHandle_t s_wifi_retry_task_handle = NULL;
static sr_runtime_ctx_t s_sr_ctx;
static const int16_t s_wake_prompt_sine_table[WAKE_PROMPT_TONE_TABLE_SIZE] = {
    0, 6393, 12539, 18204, 23170, 27245, 30273, 32137,
    32767, 32137, 30273, 27245, 23170, 18204, 12539, 6393,
    0, -6393, -12539, -18204, -23170, -27245, -30273, -32137,
    -32767, -32137, -30273, -27245, -23170, -18204, -12539, -6393
};

static const wifi_profile_t s_wifi_profiles[WIFI_PROFILE_COUNT] = {
    {
        .ssid = CONFIG_GLASS_WIFI_PRIMARY_SSID,
        .password = CONFIG_GLASS_WIFI_PRIMARY_PASSWORD,
    },
    {
        .ssid = CONFIG_GLASS_WIFI_FALLBACK_SSID,
        .password = CONFIG_GLASS_WIFI_FALLBACK_PASSWORD,
    },
};

static glass_runtime_config_t s_runtime_config = {
    .server_ws_uri = CONFIG_GLASS_SERVER_WS_URI,
    .device_id = CONFIG_GLASS_DEVICE_ID,
    .pair_token = CONFIG_GLASS_PAIR_TOKEN,
    .firmware_version = CONFIG_GLASS_FIRMWARE_VERSION,
    .heartbeat_interval_ms = CONFIG_GLASS_HEARTBEAT_INTERVAL_MS,
};

static void clear_reply_wait_state(void);
static void begin_reply_wait_state(void);
static bool reply_wait_timed_out(uint64_t current_ms);
static void recover_wake_listening_after_reply_timeout(uint64_t current_ms);
static void reset_control_session_state(void);
static void ensure_control_transport_started(void);
static bool init_camera(void);
static bool ensure_speaker_channel_enabled(void);
static void start_playback_stream(const char *stream_id);
static void play_wake_prompt_tone(void);
static void start_camera_stream(const char *stream_id, const char *target_ws_uri, int frame_interval_ms);
static void stop_camera_stream(const char *stream_id);
static void schedule_next_wifi_round(void);

static const char *preferred_wakenet_model_name(srmodel_list_t *models)
{
    char *wn_name = NULL;

#if CONFIG_SR_WN_WN9_HILEXIN
    wn_name = esp_srmodel_filter(models, ESP_WN_PREFIX, "hilexin");
    if (wn_name != NULL) {
        ESP_LOGI(TAG, "优先命中嗨乐鑫 WakeNet 模型: %s", wn_name);
        return wn_name;
    }
    ESP_LOGW(TAG, "未命中嗨乐鑫 WakeNet 模型，回退到首个可用模型");
#endif

    return esp_srmodel_filter(models, ESP_WN_PREFIX, NULL);
}

static bool wifi_profile_available(int index)
{
    return index >= 0 &&
           index < WIFI_PROFILE_COUNT &&
           s_wifi_profiles[index].ssid[0] != '\0';
}

static int first_available_wifi_profile(void)
{
    for (int index = 0; index < WIFI_PROFILE_COUNT; index += 1) {
        if (wifi_profile_available(index)) {
            return index;
        }
    }
    return -1;
}

static int count_available_wifi_profiles(void)
{
    int count = 0;
    for (int index = 0; index < WIFI_PROFILE_COUNT; index += 1) {
        if (wifi_profile_available(index)) {
            count += 1;
        }
    }
    return count;
}

static int next_available_wifi_profile(int current_index)
{
    for (int offset = 1; offset <= WIFI_PROFILE_COUNT; offset += 1) {
        int candidate = (current_index + offset) % WIFI_PROFILE_COUNT;
        if (wifi_profile_available(candidate)) {
            return candidate;
        }
    }
    return -1;
}

static uint64_t now_ms(void)
{
    return (uint64_t)(esp_timer_get_time() / 1000ULL);
}

static void build_runtime_token(const char *prefix, char *buffer, size_t size)
{
    uint32_t random_part = esp_random();
    s_message_sequence += 1;
    snprintf(buffer, size, "%s_%" PRIu32 "_%08" PRIx32, prefix, s_message_sequence, random_part);
}

static void build_message_id(char *buffer, size_t size)
{
    char token[48];
    build_runtime_token("msg", token, sizeof(token));
    snprintf(buffer, size, "%s_%s", token, s_runtime_config.device_id);
}

static bool split_server_uri(
    const char *ws_uri,
    char *scheme_buffer,
    size_t scheme_size,
    char *authority_buffer,
    size_t authority_size
)
{
    const char *prefix = NULL;
    const char *authority_start = NULL;
    const char *authority_end = NULL;
    size_t authority_len;

    if (strncmp(ws_uri, "ws://", 5) == 0) {
        prefix = "ws";
        authority_start = ws_uri + 5;
    } else if (strncmp(ws_uri, "wss://", 6) == 0) {
        prefix = "wss";
        authority_start = ws_uri + 6;
    } else {
        return false;
    }

    authority_end = strchr(authority_start, '/');
    authority_len = authority_end != NULL ? (size_t)(authority_end - authority_start) : strlen(authority_start);
    if (authority_len == 0 || authority_len >= authority_size) {
        return false;
    }

    strlcpy(scheme_buffer, prefix, scheme_size);
    memcpy(authority_buffer, authority_start, authority_len);
    authority_buffer[authority_len] = '\0';
    return true;
}

static bool build_audio_ws_uri(char *buffer, size_t size)
{
    char scheme[8];
    char authority[128];
    if (!split_server_uri(s_runtime_config.server_ws_uri, scheme, sizeof(scheme), authority, sizeof(authority))) {
        return false;
    }
    snprintf(buffer, size, "%s://%s/ws_audio?device_id=%s", scheme, authority, s_runtime_config.device_id);
    return true;
}

static bool build_stream_wav_url(const char *stream_id, char *buffer, size_t size)
{
    char scheme[8];
    char authority[128];
    const char *http_scheme;
    if (!split_server_uri(s_runtime_config.server_ws_uri, scheme, sizeof(scheme), authority, sizeof(authority))) {
        return false;
    }

    http_scheme = strcmp(scheme, "wss") == 0 ? "https" : "http";
    snprintf(
        buffer,
        size,
        "%s://%s/stream.wav?device_id=%s&stream_id=%s",
        http_scheme,
        authority,
        s_runtime_config.device_id,
        stream_id
    );
    return true;
}

static cJSON *build_endpoint_json(const char *device_id, const char *device_type, const char *module)
{
    cJSON *endpoint = cJSON_CreateObject();
    if (endpoint == NULL) {
        return NULL;
    }
    cJSON_AddStringToObject(endpoint, "device_id", device_id);
    cJSON_AddStringToObject(endpoint, "device_type", device_type);
    cJSON_AddStringToObject(endpoint, "module", module);
    return endpoint;
}

static char *build_control_message_json(
    const char *semantic,
    const char *name,
    const char *session_id,
    cJSON *payload
)
{
    char message_id[96];
    cJSON *root = cJSON_CreateObject();
    cJSON *source = NULL;
    cJSON *target = NULL;
    char *json_text = NULL;

    if (root == NULL) {
        goto cleanup;
    }

    build_message_id(message_id, sizeof(message_id));
    source = build_endpoint_json(s_runtime_config.device_id, "glass", "glass-api");
    target = build_endpoint_json(CONTROL_TARGET_DEVICE_ID, "server", "server-api");
    if (source == NULL || target == NULL) {
        goto cleanup;
    }

    cJSON_AddStringToObject(root, "version", "v1");
    cJSON_AddStringToObject(root, "message_id", message_id);
    cJSON_AddStringToObject(root, "channel", "control");
    cJSON_AddStringToObject(root, "semantic", semantic);
    cJSON_AddStringToObject(root, "name", name);
    cJSON_AddItemToObject(root, "source", source);
    cJSON_AddItemToObject(root, "target", target);
    source = NULL;
    target = NULL;
    cJSON_AddNumberToObject(root, "ts", (double)now_ms());
    if (session_id != NULL && session_id[0] != '\0') {
        cJSON_AddStringToObject(root, "session_id", session_id);
    }
    if (payload == NULL) {
        payload = cJSON_CreateObject();
    }
    cJSON_AddItemToObject(root, "payload", payload);
    payload = NULL;
    cJSON_AddItemToObject(root, "meta", cJSON_CreateObject());

    json_text = cJSON_PrintUnformatted(root);

cleanup:
    cJSON_Delete(source);
    cJSON_Delete(target);
    cJSON_Delete(payload);
    cJSON_Delete(root);
    return json_text;
}

static void send_control_message_json(char *json_text, const char *name)
{
    if (json_text == NULL) {
        ESP_LOGE(TAG, "构造控制消息失败: name=%s", name);
        return;
    }
    if (s_ws_client == NULL || !esp_websocket_client_is_connected(s_ws_client)) {
        ESP_LOGW(TAG, "控制连接未建立，跳过发送: %s", name);
        free(json_text);
        return;
    }

    int written = esp_websocket_client_send_text(
        s_ws_client,
        json_text,
        strlen(json_text),
        pdMS_TO_TICKS(3000)
    );
    if (written < 0) {
        ESP_LOGE(TAG, "发送控制消息失败: %s", name);
    } else if (strcmp(name, "device.heartbeat") != 0) {
        ESP_LOGD(TAG, "已发送控制消息: %s", name);
    }
    free(json_text);
}

static void send_register_message(void)
{
    cJSON *payload = cJSON_CreateObject();
    cJSON *auth = cJSON_CreateObject();
    if (payload == NULL || auth == NULL) {
        cJSON_Delete(payload);
        cJSON_Delete(auth);
        ESP_LOGE(TAG, "构造注册消息失败");
        return;
    }

    cJSON_AddStringToObject(payload, "device_id", s_runtime_config.device_id);
    cJSON_AddStringToObject(payload, "device_type", "glass");
    cJSON_AddStringToObject(payload, "firmware_version", s_runtime_config.firmware_version);
    cJSON_AddStringToObject(auth, "mode", "pair_token");
    cJSON_AddStringToObject(auth, "pair_token", s_runtime_config.pair_token);
    cJSON_AddItemToObject(payload, "auth", auth);

    send_control_message_json(
        build_control_message_json("request", "device.register", NULL, payload),
        "device.register"
    );
}

static void send_heartbeat_message(void)
{
    cJSON *payload = cJSON_CreateObject();
    if (payload == NULL) {
        ESP_LOGE(TAG, "构造心跳消息失败");
        return;
    }

    cJSON_AddStringToObject(payload, "device_id", s_runtime_config.device_id);
    send_control_message_json(
        build_control_message_json("notify", "device.heartbeat", NULL, payload),
        "device.heartbeat"
    );
}

static void send_voice_session_opened_message(const char *session_id)
{
    cJSON *payload = cJSON_CreateObject();
    if (payload == NULL) {
        ESP_LOGE(TAG, "构造 voice.session.opened 失败");
        return;
    }

    cJSON_AddStringToObject(payload, "device_id", s_runtime_config.device_id);
    send_control_message_json(
        build_control_message_json("notify", "voice.session.opened", session_id, payload),
        "voice.session.opened"
    );
}

static void send_realtime_session_opened_message(const char *session_id)
{
    cJSON *payload = cJSON_CreateObject();
    cJSON *capabilities = cJSON_CreateObject();
    if (payload == NULL || capabilities == NULL) {
        cJSON_Delete(payload);
        cJSON_Delete(capabilities);
        ESP_LOGE(TAG, "构造 voice.realtime.session.opened 失败");
        return;
    }

    cJSON_AddStringToObject(payload, "device_id", s_runtime_config.device_id);
    cJSON_AddStringToObject(payload, "accepted_mode", "half_duplex");
    cJSON_AddBoolToObject(capabilities, "aec", false);
    cJSON_AddBoolToObject(capabilities, "vad", true);
    cJSON_AddBoolToObject(capabilities, "barge_in", false);
    cJSON_AddBoolToObject(capabilities, "output_cancel", false);
    cJSON_AddBoolToObject(capabilities, "continuous_dialog", s_realtime_semantic_dialog_enabled);
    cJSON_AddStringToObject(
        capabilities,
        "turn_detection_owner",
        s_realtime_semantic_dialog_enabled ? "omni_realtime" : "endpoint"
    );
    cJSON_AddItemToObject(payload, "capabilities", capabilities);

    send_control_message_json(
        build_control_message_json("notify", "voice.realtime.session.opened", session_id, payload),
        "voice.realtime.session.opened"
    );
}

static bool payload_requests_omni_semantic_dialog(const cJSON *payload)
{
    const cJSON *input = payload != NULL ? cJSON_GetObjectItemCaseSensitive(payload, "input") : NULL;
    const cJSON *conversation_mode = input != NULL
        ? cJSON_GetObjectItemCaseSensitive(input, "conversation_mode")
        : NULL;
    const cJSON *turn_detection = input != NULL
        ? cJSON_GetObjectItemCaseSensitive(input, "turn_detection")
        : NULL;
    const cJSON *owner = turn_detection != NULL
        ? cJSON_GetObjectItemCaseSensitive(turn_detection, "owner")
        : NULL;

    return cJSON_IsString(conversation_mode) &&
           strcmp(conversation_mode->valuestring, "realtime_semantic_vad") == 0 &&
           cJSON_IsString(owner) &&
           strcmp(owner->valuestring, "omni_realtime") == 0;
}

static void send_audio_segment_started_message(const char *segment_id)
{
    cJSON *payload = cJSON_CreateObject();
    cJSON *wake_word = cJSON_CreateObject();
    if (payload == NULL || wake_word == NULL) {
        cJSON_Delete(payload);
        cJSON_Delete(wake_word);
        ESP_LOGE(TAG, "构造 sensor.audio.segment.started 失败");
        return;
    }
    if (s_current_session_id[0] == '\0') {
        ESP_LOGW(TAG, "session_id 为空，跳过发送 sensor.audio.segment.started");
        cJSON_Delete(payload);
        cJSON_Delete(wake_word);
        return;
    }
    if (s_current_stream_id[0] == '\0') {
        build_runtime_token("stream", s_current_stream_id, sizeof(s_current_stream_id));
    }

    cJSON_AddStringToObject(payload, "device_id", s_runtime_config.device_id);
    cJSON_AddStringToObject(payload, "stream_id", s_current_stream_id);
    cJSON_AddStringToObject(payload, "segment_id", segment_id);
    cJSON_AddNumberToObject(payload, "sample_rate", SR_SAMPLE_RATE_HZ);
    cJSON_AddNumberToObject(payload, "channels", 1);
    cJSON_AddStringToObject(payload, "codec", "pcm16");
    cJSON_AddStringToObject(wake_word, "engine", "esp-sr-wakenet");
    cJSON_AddStringToObject(
        wake_word,
        "model",
        s_sr_ctx.wake_model_name[0] != '\0' ? s_sr_ctx.wake_model_name : "unknown"
    );
    cJSON_AddItemToObject(payload, "wake_word", wake_word);

    send_control_message_json(
        build_control_message_json("notify", "sensor.audio.segment.started", s_current_session_id, payload),
        "sensor.audio.segment.started"
    );
}

static void send_audio_segment_finished_message(
    const char *segment_id,
    int duration_ms,
    uint32_t pcm_bytes,
    const char *finish_reason
)
{
    cJSON *payload = cJSON_CreateObject();
    if (payload == NULL) {
        ESP_LOGE(TAG, "构造 sensor.audio.segment.finished 失败");
        return;
    }
    if (s_current_session_id[0] == '\0' || s_current_stream_id[0] == '\0') {
        ESP_LOGW(TAG, "session_id 或 stream_id 为空，跳过发送 sensor.audio.segment.finished");
        cJSON_Delete(payload);
        return;
    }

    cJSON_AddStringToObject(payload, "device_id", s_runtime_config.device_id);
    cJSON_AddStringToObject(payload, "stream_id", s_current_stream_id);
    cJSON_AddStringToObject(payload, "segment_id", segment_id);
    cJSON_AddNumberToObject(payload, "duration_ms", duration_ms);
    cJSON_AddNumberToObject(payload, "bytes", (double)pcm_bytes);
    cJSON_AddStringToObject(payload, "finish_reason", finish_reason);

    send_control_message_json(
        build_control_message_json("notify", "sensor.audio.segment.finished", s_current_session_id, payload),
        "sensor.audio.segment.finished"
    );
}

static void send_actuator_audio_state_message(
    const char *name,
    const char *stream_id,
    const char *state,
    const char *reason
)
{
    cJSON *payload = cJSON_CreateObject();
    if (payload == NULL) {
        ESP_LOGE(TAG, "构造 %s 失败", name);
        return;
    }
    if (s_current_session_id[0] == '\0' || stream_id == NULL || stream_id[0] == '\0') {
        ESP_LOGW(TAG, "session_id 或 stream_id 为空，跳过发送 %s", name);
        cJSON_Delete(payload);
        return;
    }

    cJSON_AddStringToObject(payload, "device_id", s_runtime_config.device_id);
    cJSON_AddStringToObject(payload, "stream_id", stream_id);
    if (state != NULL && state[0] != '\0') {
        cJSON_AddStringToObject(payload, "state", state);
    }
    if (reason != NULL && reason[0] != '\0') {
        cJSON_AddStringToObject(payload, "reason", reason);
    }
    send_control_message_json(
        build_control_message_json("notify", name, s_current_session_id, payload),
        name
    );
}

static bool init_camera(void)
{
    camera_config_t config = {0};
    esp_err_t err;
    sensor_t *sensor = NULL;

    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = CAM_Y2_GPIO;
    config.pin_d1 = CAM_Y3_GPIO;
    config.pin_d2 = CAM_Y4_GPIO;
    config.pin_d3 = CAM_Y5_GPIO;
    config.pin_d4 = CAM_Y6_GPIO;
    config.pin_d5 = CAM_Y7_GPIO;
    config.pin_d6 = CAM_Y8_GPIO;
    config.pin_d7 = CAM_Y9_GPIO;
    config.pin_xclk = CAM_XCLK_GPIO;
    config.pin_pclk = CAM_PCLK_GPIO;
    config.pin_vsync = CAM_VSYNC_GPIO;
    config.pin_href = CAM_HREF_GPIO;
    config.pin_sccb_sda = CAM_SIOD_GPIO;
    config.pin_sccb_scl = CAM_SIOC_GPIO;
    config.pin_pwdn = CAM_PWDN_GPIO;
    config.pin_reset = CAM_RESET_GPIO;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = CAMERA_FRAME_SIZE;
    config.jpeg_quality = CAMERA_JPEG_QUALITY;
    /*
     * 当前 SDK运行时 默认按“按需抓拍”模式工作，而不是持续视频流模式。
     * 这里使用单缓冲 + WHEN_EMPTY，避免摄像头在空闲时持续产出帧，
     * 但上层没有持续消费，最终触发 cam_hal 的 FB-OVF 日志。
     */
    config.fb_count = CAMERA_FB_COUNT;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

    err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "摄像头初始化失败: %s", esp_err_to_name(err));
        return false;
    }

    sensor = esp_camera_sensor_get();
    if (sensor != NULL) {
        sensor->set_hmirror(sensor, 1);
        sensor->set_vflip(sensor, 0);
        sensor->set_brightness(sensor, 0);
        sensor->set_contrast(sensor, 1);
        sensor->set_saturation(sensor, 1);
        sensor->set_gain_ctrl(sensor, 1);
        sensor->set_exposure_ctrl(sensor, 0);
        sensor->set_whitebal(sensor, 1);
        sensor->set_awb_gain(sensor, 1);
        sensor->set_aec2(sensor, 0);
        sensor->set_aec_value(sensor, 40);
    }

    ESP_LOGI(
        TAG,
        "摄像头初始化完成: frame_size=%d jpeg_quality=%d fb_count=%d grab_mode=%d",
        CAMERA_FRAME_SIZE,
        CAMERA_JPEG_QUALITY,
        CAMERA_FB_COUNT,
        CAMERA_GRAB_WHEN_EMPTY
    );
    return true;
}

static bool base64_encode_bytes(const uint8_t *input, size_t input_size, char **output)
{
    size_t encoded_capacity = 4 * ((input_size + 2) / 3) + 1;
    size_t encoded_length = 0;
    int ret;
    char *buffer = NULL;

    if (output == NULL) {
        return false;
    }
    *output = NULL;

    buffer = heap_caps_malloc(encoded_capacity, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (buffer == NULL) {
        buffer = heap_caps_malloc(encoded_capacity, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    if (buffer == NULL) {
        ESP_LOGE(TAG, "分配图片 base64 缓冲失败: bytes=%u", (unsigned)input_size);
        return false;
    }

    ret = mbedtls_base64_encode(
        (unsigned char *)buffer,
        encoded_capacity,
        &encoded_length,
        input,
        input_size
    );
    if (ret != 0) {
        ESP_LOGE(TAG, "图片 base64 编码失败: ret=%d", ret);
        heap_caps_free(buffer);
        return false;
    }
    buffer[encoded_length] = '\0';
    *output = buffer;
    return true;
}

static void send_camera_captured_message(
    const char *session_id,
    const char *request_id,
    bool ok,
    const char *mime_type,
    const char *codec,
    int width,
    int height,
    const char *image_base64,
    const char *error_message
)
{
    cJSON *payload = cJSON_CreateObject();
    cJSON *error = NULL;
    if (payload == NULL) {
        ESP_LOGE(TAG, "构造 sensor.camera.captured 失败");
        return;
    }

    cJSON_AddStringToObject(payload, "device_id", s_runtime_config.device_id);
    cJSON_AddStringToObject(payload, "request_id", request_id);
    cJSON_AddBoolToObject(payload, "ok", ok);
    if (ok) {
        cJSON_AddStringToObject(payload, "mime_type", mime_type);
        cJSON_AddStringToObject(payload, "codec", codec);
        cJSON_AddNumberToObject(payload, "width", width);
        cJSON_AddNumberToObject(payload, "height", height);
        cJSON_AddStringToObject(payload, "image_base64", image_base64);
    } else {
        error = cJSON_CreateObject();
        if (error == NULL) {
            cJSON_Delete(payload);
            ESP_LOGE(TAG, "构造抓拍错误对象失败");
            return;
        }
        cJSON_AddStringToObject(error, "code", "CAMERA_CAPTURE_FAILED");
        cJSON_AddStringToObject(error, "message", error_message != NULL ? error_message : "设备抓拍失败");
        cJSON_AddItemToObject(payload, "error", error);
    }

    send_control_message_json(
        build_control_message_json("notify", "sensor.camera.captured", session_id, payload),
        "sensor.camera.captured"
    );
}

typedef struct {
    char request_id[64];
    char session_id[64];
    char reason[64];
} camera_capture_task_arg_t;

static void camera_capture_task(void *arg)
{
    camera_capture_task_arg_t *task_arg = (camera_capture_task_arg_t *)arg;
    camera_fb_t *fb = NULL;
    char *image_base64 = NULL;

    if (task_arg == NULL) {
        vTaskDelete(NULL);
        return;
    }

    if (!s_camera_initialized) {
        send_camera_captured_message(
            task_arg->session_id,
            task_arg->request_id,
            false,
            NULL,
            NULL,
            0,
            0,
            NULL,
            "摄像头尚未初始化"
        );
        goto cleanup;
    }

    ESP_LOGI(TAG, "开始执行单次抓拍: request_id=%s reason=%s", task_arg->request_id, task_arg->reason);
    fb = esp_camera_fb_get();
    if (fb == NULL) {
        send_camera_captured_message(
            task_arg->session_id,
            task_arg->request_id,
            false,
            NULL,
            NULL,
            0,
            0,
            NULL,
            "摄像头抓拍失败，未拿到图像帧"
        );
        goto cleanup;
    }
    if (fb->format != PIXFORMAT_JPEG) {
        send_camera_captured_message(
            task_arg->session_id,
            task_arg->request_id,
            false,
            NULL,
            NULL,
            0,
            0,
            NULL,
            "摄像头返回了非 JPEG 图像"
        );
        goto cleanup;
    }
    if (!base64_encode_bytes(fb->buf, fb->len, &image_base64)) {
        send_camera_captured_message(
            task_arg->session_id,
            task_arg->request_id,
            false,
            NULL,
            NULL,
            0,
            0,
            NULL,
            "图片编码失败"
        );
        goto cleanup;
    }

    send_camera_captured_message(
        task_arg->session_id,
        task_arg->request_id,
        true,
        "image/jpeg",
        "jpeg",
        fb->width,
        fb->height,
        image_base64,
        NULL
    );
    ESP_LOGI(
        TAG,
        "单次抓拍完成: request_id=%s width=%d height=%d bytes=%u",
        task_arg->request_id,
        fb->width,
        fb->height,
        (unsigned)fb->len
    );

cleanup:
    if (fb != NULL) {
        esp_camera_fb_return(fb);
    }
    heap_caps_free(image_base64);
    s_camera_capture_busy = false;
    free(task_arg);
    vTaskDelete(NULL);
}

static bool send_audio_chunk_frame(
    const char *stream_id,
    const char *segment_id,
    const uint8_t *payload,
    size_t payload_size,
    uint32_t chunk_seq,
    bool final
)
{
    cJSON *header = cJSON_CreateObject();
    char *header_json = NULL;
    uint8_t *raw = NULL;
    uint32_t header_len = 0;
    int written = -1;

    if (s_audio_ws_client == NULL || !esp_websocket_client_is_connected(s_audio_ws_client)) {
        return false;
    }
    if (header == NULL) {
        ESP_LOGE(TAG, "构造音频帧头失败");
        return false;
    }

    cJSON_AddStringToObject(header, "version", "v1");
    cJSON_AddStringToObject(header, "stream_id", stream_id);
    cJSON_AddStringToObject(header, "segment_id", segment_id);
    cJSON_AddStringToObject(header, "frame_type", "audio_chunk");
    cJSON_AddNumberToObject(header, "seq", (double)chunk_seq);
    cJSON_AddNumberToObject(header, "ts_ms", (double)now_ms());
    cJSON_AddStringToObject(header, "codec", "pcm16le");
    cJSON_AddNumberToObject(header, "sample_rate", SR_SAMPLE_RATE_HZ);
    cJSON_AddNumberToObject(header, "channels", 1);
    cJSON_AddNumberToObject(header, "payload_size", (double)payload_size);
    cJSON_AddBoolToObject(header, "final", final);

    header_json = cJSON_PrintUnformatted(header);
    if (header_json == NULL) {
        ESP_LOGE(TAG, "序列化音频帧头失败");
        goto cleanup;
    }

    header_len = (uint32_t)strlen(header_json);
    raw = heap_caps_malloc(4 + header_len + payload_size, MALLOC_CAP_8BIT);
    if (raw == NULL) {
        ESP_LOGE(TAG, "分配音频帧缓冲失败");
        goto cleanup;
    }

    raw[0] = (uint8_t)((header_len >> 24) & 0xFF);
    raw[1] = (uint8_t)((header_len >> 16) & 0xFF);
    raw[2] = (uint8_t)((header_len >> 8) & 0xFF);
    raw[3] = (uint8_t)(header_len & 0xFF);
    memcpy(raw + 4, header_json, header_len);
    memcpy(raw + 4 + header_len, payload, payload_size);

    written = esp_websocket_client_send_bin(
        s_audio_ws_client,
        (const char *)raw,
        4 + (int)header_len + (int)payload_size,
        pdMS_TO_TICKS(3000)
    );
    if (written < 0) {
        ESP_LOGW(TAG, "发送 audio_chunk 失败: seq=%" PRIu32, chunk_seq);
    }

cleanup:
    heap_caps_free(raw);
    free(header_json);
    cJSON_Delete(header);
    return written >= 0;
}

static bool send_camera_frame(
    const char *stream_id,
    const uint8_t *payload,
    size_t payload_size,
    uint32_t frame_seq,
    uint16_t width,
    uint16_t height,
    bool final
)
{
    cJSON *header = cJSON_CreateObject();
    char *header_json = NULL;
    uint8_t *raw = NULL;
    uint32_t header_len = 0;
    int written = -1;

    if (s_camera_ws_client == NULL || !esp_websocket_client_is_connected(s_camera_ws_client)) {
        return false;
    }
    if (header == NULL) {
        ESP_LOGE(TAG, "构造相机帧头失败");
        return false;
    }

    cJSON_AddStringToObject(header, "version", "v1");
    cJSON_AddStringToObject(header, "stream_id", stream_id);
    cJSON_AddStringToObject(header, "frame_type", "camera_frame");
    cJSON_AddNumberToObject(header, "seq", (double)frame_seq);
    cJSON_AddNumberToObject(header, "ts_ms", (double)now_ms());
    cJSON_AddStringToObject(header, "codec", "jpeg");
    cJSON_AddNumberToObject(header, "payload_size", (double)payload_size);
    cJSON_AddBoolToObject(header, "final", final);
    cJSON_AddNumberToObject(header, "width", (double)width);
    cJSON_AddNumberToObject(header, "height", (double)height);
    cJSON_AddNumberToObject(header, "frame_index", (double)frame_seq);

    header_json = cJSON_PrintUnformatted(header);
    if (header_json == NULL) {
        ESP_LOGE(TAG, "序列化相机帧头失败");
        goto cleanup;
    }

    header_len = (uint32_t)strlen(header_json);
    raw = heap_caps_malloc(4 + header_len + payload_size, MALLOC_CAP_8BIT);
    if (raw == NULL) {
        ESP_LOGE(TAG, "分配相机帧缓冲失败");
        goto cleanup;
    }

    raw[0] = (uint8_t)((header_len >> 24) & 0xFF);
    raw[1] = (uint8_t)((header_len >> 16) & 0xFF);
    raw[2] = (uint8_t)((header_len >> 8) & 0xFF);
    raw[3] = (uint8_t)(header_len & 0xFF);
    memcpy(raw + 4, header_json, header_len);
    memcpy(raw + 4 + header_len, payload, payload_size);

    written = esp_websocket_client_send_bin(
        s_camera_ws_client,
        (const char *)raw,
        4 + (int)header_len + (int)payload_size,
        pdMS_TO_TICKS(3000)
    );
    if (written < 0) {
        ESP_LOGW(TAG, "发送 camera_frame 失败: seq=%" PRIu32, frame_seq);
    }

cleanup:
    heap_caps_free(raw);
    free(header_json);
    cJSON_Delete(header);
    return written >= 0;
}

static esp_err_t apply_wifi_profile(int index)
{
    wifi_config_t wifi_config = {0};
    if (!wifi_profile_available(index)) {
        return ESP_ERR_INVALID_ARG;
    }

    strlcpy((char *)wifi_config.sta.ssid, s_wifi_profiles[index].ssid, sizeof(wifi_config.sta.ssid));
    strlcpy(
        (char *)wifi_config.sta.password,
        s_wifi_profiles[index].password,
        sizeof(wifi_config.sta.password)
    );
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.pmf_cfg.capable = true;
    wifi_config.sta.pmf_cfg.required = false;
    return esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
}

static void connect_active_wifi_profile(const char *reason)
{
    if (!wifi_profile_available(s_active_wifi_profile)) {
        int first_profile = first_available_wifi_profile();
        if (first_profile < 0) {
            ESP_LOGE(TAG, "没有可用的 WiFi 配置，无法发起连接");
            return;
        }
        s_active_wifi_profile = first_profile;
    }

    ESP_LOGI(
        TAG,
        "开始连接 WiFi: ssid=%s reason=%s",
        s_wifi_profiles[s_active_wifi_profile].ssid,
        reason
    );
    ESP_ERROR_CHECK(apply_wifi_profile(s_active_wifi_profile));
    ESP_ERROR_CHECK(esp_wifi_connect());
}

static void wifi_retry_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(WIFI_RETRY_DELAY_MS));
    s_wifi_retry_task_handle = NULL;
    if ((xEventGroupGetBits(s_wifi_event_group) & WIFI_CONNECTED_BIT) != 0) {
        ESP_LOGD(TAG, "WiFi 在等待期间已恢复，跳过本次延迟重试");
        vTaskDelete(NULL);
        return;
    }
    connect_active_wifi_profile("round_retry");
    vTaskDelete(NULL);
}

static void schedule_next_wifi_round(void)
{
    int first_profile = first_available_wifi_profile();
    if (first_profile < 0) {
        ESP_LOGE(TAG, "没有可用的 WiFi 配置，无法安排下一轮重试");
        xEventGroupSetBits(s_wifi_event_group, WIFI_FAILED_BIT);
        return;
    }

    s_active_wifi_profile = first_profile;
    s_wifi_round_attempt_count = 0;
    if (s_wifi_retry_task_handle != NULL) {
        ESP_LOGD(TAG, "WiFi 延迟重试任务已在等待，本次不重复创建");
        return;
    }

    ESP_LOGW(TAG, "本轮 WiFi 均连接失败，等待 %d ms 后重新开始轮询", WIFI_RETRY_DELAY_MS);
    if (xTaskCreate(wifi_retry_task, "wifi_retry_task", 4096, NULL, 4, &s_wifi_retry_task_handle) != pdPASS) {
        s_wifi_retry_task_handle = NULL;
        ESP_LOGE(TAG, "创建 WiFi 延迟重试任务失败，立即重试首个 WiFi");
        connect_active_wifi_profile("round_retry_fallback");
    }
}

static void wifi_event_handler(
    void *arg,
    esp_event_base_t event_base,
    int32_t event_id,
    void *event_data
)
{
    (void)arg;

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        connect_active_wifi_profile("sta_start");
        return;
    }

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        reset_control_session_state();

        int available_profiles = count_available_wifi_profiles();
        if (available_profiles <= 0) {
            ESP_LOGE(TAG, "没有可用的 WiFi 配置，无法继续重连");
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAILED_BIT);
            return;
        }

        s_wifi_round_attempt_count += 1;
        ESP_LOGW(
            TAG,
            "WiFi 断开: ssid=%s round_attempt=%d/%d",
            s_wifi_profiles[s_active_wifi_profile].ssid,
            s_wifi_round_attempt_count,
            available_profiles
        );

        if (s_wifi_round_attempt_count < available_profiles) {
            int next_profile = next_available_wifi_profile(s_active_wifi_profile);
            if (next_profile >= 0 && next_profile != s_active_wifi_profile) {
                s_active_wifi_profile = next_profile;
                connect_active_wifi_profile("switch_profile");
                return;
            }
        }

        schedule_next_wifi_round();
        return;
    }

    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        s_wifi_round_attempt_count = 0;
        ESP_LOGI(
            TAG,
            "WiFi 已获取 IP，准备建立控制连接: ip=" IPSTR,
            IP2STR(&event->ip_info.ip)
        );
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void log_runtime_config(void)
{
    ESP_LOGI(
        TAG,
        "config: device_id=%s server_ws_uri=%s heartbeat_interval_ms=%" PRIu32,
        s_runtime_config.device_id,
        s_runtime_config.server_ws_uri,
        s_runtime_config.heartbeat_interval_ms
    );
}

static bool init_wifi(void)
{
    EventBits_t bits;
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();

    s_active_wifi_profile = first_available_wifi_profile();
    if (s_active_wifi_profile < 0) {
        ESP_LOGE(TAG, "WiFi 名称为空，请先在本地配置文件中设置至少一个 WiFi");
        return false;
    }

    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(
        esp_event_handler_instance_register(
            WIFI_EVENT,
            ESP_EVENT_ANY_ID,
            &wifi_event_handler,
            NULL,
            &s_wifi_event_instance
        )
    );
    ESP_ERROR_CHECK(
        esp_event_handler_instance_register(
            IP_EVENT,
            IP_EVENT_STA_GOT_IP,
            &wifi_event_handler,
            NULL,
            &s_ip_event_instance
        )
    );

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(apply_wifi_profile(s_active_wifi_profile));
    ESP_ERROR_CHECK(esp_wifi_start());

    bits = xEventGroupWaitBits(
        s_wifi_event_group,
        WIFI_CONNECTED_BIT,
        pdFALSE,
        pdFALSE,
        portMAX_DELAY
    );
    if ((bits & WIFI_CONNECTED_BIT) == 0) {
        ESP_LOGE(TAG, "WiFi 初始化失败，停止后续控制连接流程");
        return false;
    }
    return true;
}

static esp_err_t init_mic_i2s(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, NULL, &s_mic_rx_chan), TAG, "new mic channel failed");

    i2s_pdm_rx_config_t pdm_rx_cfg = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(SR_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .clk = MIC_PDM_CLK_GPIO,
            .din = MIC_PDM_DATA_GPIO,
            .invert_flags = {
                .clk_inv = false,
            },
        },
    };
    ESP_RETURN_ON_ERROR(
        i2s_channel_init_pdm_rx_mode(s_mic_rx_chan, &pdm_rx_cfg),
        TAG,
        "init mic pdm mode failed"
    );
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_mic_rx_chan), TAG, "enable mic channel failed");
    ESP_LOGI(TAG, "MIC ready: PDM RX, sr=%d, clk=%d, data=%d", SR_SAMPLE_RATE_HZ, MIC_PDM_CLK_GPIO, MIC_PDM_DATA_GPIO);
    return ESP_OK;
}

static esp_err_t init_speaker_i2s(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SR_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = SPK_I2S_BCLK_GPIO,
            .ws = SPK_I2S_LRCK_GPIO,
            .dout = SPK_I2S_DOUT_GPIO,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    chan_cfg.auto_clear_after_cb = true;

    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_spk_tx_chan, NULL), TAG, "new speaker channel failed");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_spk_tx_chan, &std_cfg), TAG, "init std tx mode failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_spk_tx_chan), TAG, "enable speaker channel failed");
    s_speaker_channel_enabled = true;
    ESP_LOGI(
        TAG,
        "Speaker ready: STD TX, sr=%d, bclk=%d, lrck=%d, dout=%d",
        SR_SAMPLE_RATE_HZ,
        SPK_I2S_BCLK_GPIO,
        SPK_I2S_LRCK_GPIO,
        SPK_I2S_DOUT_GPIO
    );
    return ESP_OK;
}

static void mono16_to_stereo32_msb(const int16_t *input, size_t sample_count, int32_t *output, float gain)
{
    for (size_t index = 0; index < sample_count; index += 1) {
        int32_t sample = (int32_t)((float)input[index] * gain);
        int32_t stereo_value = sample << 16;
        output[index * 2] = stereo_value;
        output[index * 2 + 1] = stereo_value;
    }
}

// 播放首次唤醒轻提示音。只在 WakeNet 命中后调用，连续对话窗口内的 VAD 追问不会重复提示。
static void play_wake_prompt_tone(void)
{
#if CONFIG_GLASS_WAKE_PROMPT_TONE_ENABLE
    int16_t *mono_buffer = NULL;
    int32_t *stereo_buffer = NULL;
    if (s_playback_active || s_playback_task_running) {
        return;
    }
    if (!ensure_speaker_channel_enabled()) {
        return;
    }

    mono_buffer = heap_caps_malloc(AUDIO_FRAME_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (mono_buffer == NULL) {
        mono_buffer = heap_caps_malloc(AUDIO_FRAME_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    stereo_buffer = heap_caps_malloc(AUDIO_FRAME_SAMPLES * 2 * sizeof(int32_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (stereo_buffer == NULL) {
        stereo_buffer = heap_caps_malloc(AUDIO_FRAME_SAMPLES * 2 * sizeof(int32_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (mono_buffer == NULL || stereo_buffer == NULL) {
        ESP_LOGW(TAG, "唤醒提示音缓冲分配失败，跳过提示音");
        heap_caps_free(mono_buffer);
        heap_caps_free(stereo_buffer);
        return;
    }

    const int total_samples = (SR_SAMPLE_RATE_HZ * CONFIG_GLASS_WAKE_PROMPT_TONE_DURATION_MS) / 1000;
    const int ramp_samples = total_samples < 160 ? total_samples / 2 : 80;
    uint32_t phase_q16 = 0;
    const uint32_t phase_step_q16 =
        (uint32_t)(((uint64_t)CONFIG_GLASS_WAKE_PROMPT_TONE_FREQ_HZ * WAKE_PROMPT_TONE_TABLE_SIZE * 65536ULL) /
                   SR_SAMPLE_RATE_HZ);
    int generated_samples = 0;

    while (generated_samples < total_samples) {
        int chunk_samples = total_samples - generated_samples;
        if (chunk_samples > AUDIO_FRAME_SAMPLES) {
            chunk_samples = AUDIO_FRAME_SAMPLES;
        }
        for (int index = 0; index < chunk_samples; index += 1) {
            int absolute_index = generated_samples + index;
            int envelope_permille = 1000;
            if (ramp_samples > 0 && absolute_index < ramp_samples) {
                envelope_permille = (absolute_index * 1000) / ramp_samples;
            } else if (ramp_samples > 0 && (total_samples - absolute_index) < ramp_samples) {
                envelope_permille = ((total_samples - absolute_index) * 1000) / ramp_samples;
            }
            uint32_t table_index = (phase_q16 >> 16) % WAKE_PROMPT_TONE_TABLE_SIZE;
            int32_t sample = s_wake_prompt_sine_table[table_index];
            sample = (sample * CONFIG_GLASS_WAKE_PROMPT_TONE_GAIN_PERMILLE * envelope_permille) / 1000000;
            mono_buffer[index] = (int16_t)sample;
            phase_q16 += phase_step_q16;
        }
        mono16_to_stereo32_msb(mono_buffer, (size_t)chunk_samples, stereo_buffer, 1.0f);
        size_t written = 0;
        esp_err_t write_err = i2s_channel_write(
            s_spk_tx_chan,
            stereo_buffer,
            (size_t)chunk_samples * 2U * sizeof(int32_t),
            &written,
            pdMS_TO_TICKS(100)
        );
        if (write_err != ESP_OK) {
            ESP_LOGW(TAG, "唤醒提示音写入失败: %s", esp_err_to_name(write_err));
            heap_caps_free(mono_buffer);
            heap_caps_free(stereo_buffer);
            return;
        }
        generated_samples += chunk_samples;
    }
    ESP_LOGI(
        TAG,
        "唤醒成功提示音已播放: duration_ms=%d freq_hz=%d",
        CONFIG_GLASS_WAKE_PROMPT_TONE_DURATION_MS,
        CONFIG_GLASS_WAKE_PROMPT_TONE_FREQ_HZ
    );
    heap_caps_free(mono_buffer);
    heap_caps_free(stereo_buffer);
#endif
}

static void reset_segment_state(segment_state_t *state)
{
    state->segment_active = false;
    state->got_speech = false;
    state->tail_silence_ms = 0;
    state->elapsed_ms = 0;
    state->segment_pcm_bytes = 0;
    state->chunk_seq = 0;
    state->segment_id[0] = '\0';
}

static void clear_reply_wait_state(void)
{
    s_reply_wait_started_ms = 0;
}

static void begin_reply_wait_state(void)
{
    s_reply_wait_started_ms = now_ms();
}

static void deactivate_continuous_dialog(const char *reason)
{
    if (!s_continuous_dialog_active) {
        return;
    }
    s_continuous_dialog_active = false;
    s_continuous_dialog_last_activity_ms = 0;
    ESP_LOGI(TAG, "连续对话窗口已关闭: reason=%s", reason != NULL ? reason : "unknown");
}

static void refresh_continuous_dialog_activity(void)
{
    if (!s_realtime_semantic_dialog_enabled) {
        return;
    }
    s_continuous_dialog_active = true;
    s_continuous_dialog_last_activity_ms = now_ms();
}

static bool reply_wait_timed_out(uint64_t current_ms)
{
    return s_reply_wait_started_ms > 0 &&
           current_ms >= s_reply_wait_started_ms &&
           (current_ms - s_reply_wait_started_ms) >= SERVER_REPLY_TIMEOUT_MS;
}

static void recover_wake_listening_after_reply_timeout(uint64_t current_ms)
{
    uint64_t waited_ms = current_ms - s_reply_wait_started_ms;
    clear_reply_wait_state();
    s_playback_active = false;
    s_current_playback_stream_id[0] = '\0';
    s_wake_listening_enabled = s_registered && s_voice_session_opened && s_sr_ctx.initialized;
    ESP_LOGW(
        TAG,
        "等待服务端回复超时，自动恢复待命监听: waited_ms=%" PRIu64 " timeout_ms=%d",
        waited_ms,
        SERVER_REPLY_TIMEOUT_MS
    );
}

static void reset_control_session_state(void)
{
    s_registered = false;
    s_voice_session_opened = false;
    s_wake_listening_enabled = false;
    s_playback_active = false;
    s_playback_task_running = false;
    s_playback_interrupt_requested = false;
    s_realtime_semantic_dialog_enabled = false;
    s_continuous_dialog_active = false;
    s_continuous_dialog_last_activity_ms = 0;
    clear_reply_wait_state();
    s_current_session_id[0] = '\0';
    s_current_stream_id[0] = '\0';
    s_current_playback_stream_id[0] = '\0';
    s_next_playback_stream_id[0] = '\0';
    s_playback_task_handle = NULL;
    s_camera_stream_active = false;
    s_camera_stream_task_running = false;
    s_camera_stream_ws_uri[0] = '\0';
    s_current_camera_stream_id[0] = '\0';
    s_camera_frame_seq = 0;
    s_camera_stream_task_handle = NULL;
    if (s_camera_ws_client != NULL) {
        esp_websocket_client_stop(s_camera_ws_client);
        esp_websocket_client_destroy(s_camera_ws_client);
        s_camera_ws_client = NULL;
    }
}

static void reset_pre_roll(pre_roll_frame_t *frames, size_t count, size_t *next_index, size_t *valid_count)
{
    for (size_t index = 0; index < count; index += 1) {
        frames[index].size = 0;
        frames[index].valid = false;
    }
    *next_index = 0;
    *valid_count = 0;
}

static void store_pre_roll_frame(
    pre_roll_frame_t *frames,
    size_t count,
    size_t *next_index,
    size_t *valid_count,
    const uint8_t *data,
    size_t size
)
{
    if (count == 0 || data == NULL || size == 0) {
        return;
    }

    if (size > sizeof(frames[0].data)) {
        size = sizeof(frames[0].data);
    }

    memcpy(frames[*next_index].data, data, size);
    frames[*next_index].size = size;
    frames[*next_index].valid = true;
    *next_index = (*next_index + 1U) % count;
    if (*valid_count < count) {
        *valid_count += 1U;
    }
}

static void flush_pre_roll_frames(
    pre_roll_frame_t *frames,
    size_t count,
    size_t next_index,
    size_t valid_count,
    segment_state_t *segment
)
{
    if (valid_count == 0 || segment == NULL) {
        return;
    }

    size_t start_index = (valid_count == count) ? next_index : 0;
    for (size_t offset = 0; offset < valid_count; offset += 1) {
        size_t frame_index = (start_index + offset) % count;
        pre_roll_frame_t *frame = &frames[frame_index];
        if (!frame->valid || frame->size == 0) {
            continue;
        }
        if (!send_audio_chunk_frame(
                s_current_stream_id,
                segment->segment_id,
                frame->data,
                frame->size,
                segment->chunk_seq,
                false)) {
            ESP_LOGW(TAG, "发送预取音频失败: seq=%" PRIu32, segment->chunk_seq);
        } else {
            segment->segment_pcm_bytes += (uint32_t)frame->size;
            segment->chunk_seq += 1U;
        }
    }
}

static void drain_and_pause_speaker(void)
{
    if (s_spk_tx_chan == NULL || !s_speaker_channel_enabled) {
        return;
    }
    esp_err_t disable_err = i2s_channel_disable(s_spk_tx_chan);
    if (disable_err != ESP_OK) {
        ESP_LOGW(TAG, "关闭扬声器通道失败: %s", esp_err_to_name(disable_err));
        return;
    }
    s_speaker_channel_enabled = false;
}

static void log_wake_gate_state(
    bool registered,
    bool voice_session_opened,
    bool wake_listening_enabled,
    bool audio_ws_ready,
    bool playback_active,
    bool has_session_id
)
{
    ESP_LOGD(
        TAG,
        "唤醒门控状态: registered=%d voice_session_opened=%d wake_listening_enabled=%d audio_ws_ready=%d playback_active=%d has_session_id=%d",
        registered,
        voice_session_opened,
        wake_listening_enabled,
        audio_ws_ready,
        playback_active,
        has_session_id
    );
}

static void log_sr_fetch_state(
    const char *phase,
    int wakeup_state,
    int vad_state,
    int data_size,
    bool segment_active,
    bool got_speech,
    int tail_silence_ms,
    int elapsed_ms
)
{
    ESP_LOGD(
        TAG,
        "SR 状态[%s]: wakeup_state=%d vad_state=%d data_size=%d segment_active=%d got_speech=%d tail_silence_ms=%d elapsed_ms=%d",
        phase,
        wakeup_state,
        vad_state,
        data_size,
        segment_active,
        got_speech,
        tail_silence_ms,
        elapsed_ms
    );
}

static void audio_websocket_event_handler(
    void *handler_args,
    esp_event_base_t base,
    int32_t event_id,
    void *event_data
)
{
    (void)handler_args;
    (void)base;
    (void)event_data;

    if (event_id == WEBSOCKET_EVENT_CONNECTED) {
        s_audio_ws_ready = true;
        if (!s_playback_active) {
            s_wake_listening_enabled = s_registered && s_voice_session_opened && s_sr_ctx.initialized;
        }
        ESP_LOGI(TAG, "音频上行连接已建立");
        return;
    }
    if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
        s_audio_ws_ready = false;
        s_wake_listening_enabled = false;
        ESP_LOGW(TAG, "音频上行连接已断开");
        return;
    }
    if (event_id == WEBSOCKET_EVENT_ERROR) {
        s_audio_ws_ready = false;
        s_wake_listening_enabled = false;
        ESP_LOGE(TAG, "音频上行连接发生错误");
    }
}

static void ensure_audio_transport_started(void)
{
    if (s_audio_transport_started) {
        if (!s_audio_ws_ready && s_audio_ws_client != NULL) {
            esp_websocket_client_stop(s_audio_ws_client);
            if (esp_websocket_client_start(s_audio_ws_client) != ESP_OK) {
                ESP_LOGW(TAG, "重新启动音频上行连接失败");
            }
        }
        return;
    }
    if (!build_audio_ws_uri(s_audio_ws_uri, sizeof(s_audio_ws_uri))) {
        ESP_LOGE(TAG, "构造音频上行地址失败: %s", s_runtime_config.server_ws_uri);
        return;
    }

    esp_websocket_client_config_t websocket_config = {
        .uri = s_audio_ws_uri,
        .buffer_size = 4096,
        .network_timeout_ms = 5000,
        .task_stack = 8192,
    };
    s_audio_ws_client = esp_websocket_client_init(&websocket_config);
    if (s_audio_ws_client == NULL) {
        ESP_LOGE(TAG, "创建音频上行客户端失败");
        return;
    }

    ESP_ERROR_CHECK(
        esp_websocket_register_events(
            s_audio_ws_client,
            WEBSOCKET_EVENT_ANY,
            audio_websocket_event_handler,
            NULL
        )
    );
    ESP_ERROR_CHECK(esp_websocket_client_start(s_audio_ws_client));
    s_audio_transport_started = true;
}

static void camera_websocket_event_handler(
    void *handler_args,
    esp_event_base_t base,
    int32_t event_id,
    void *event_data
)
{
    (void)handler_args;
    (void)base;
    (void)event_data;

    if (event_id == WEBSOCKET_EVENT_CONNECTED) {
        ESP_LOGI(TAG, "相机流连接已建立");
        return;
    }
    if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
        ESP_LOGW(TAG, "相机流连接已断开");
        return;
    }
    if (event_id == WEBSOCKET_EVENT_ERROR) {
        ESP_LOGE(TAG, "相机流连接发生错误");
    }
}

static void camera_stream_task(void *arg)
{
    (void)arg;
    camera_fb_t *fb = NULL;

    while (s_camera_stream_active) {
        if (s_camera_ws_client == NULL || !esp_websocket_client_is_connected(s_camera_ws_client)) {
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }

        fb = esp_camera_fb_get();
        if (fb == NULL) {
            ESP_LOGW(TAG, "获取相机帧失败");
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }

        if (!send_camera_frame(
                s_current_camera_stream_id,
                fb->buf,
                fb->len,
                s_camera_frame_seq++,
                (uint16_t)fb->width,
                (uint16_t)fb->height,
                false)) {
            ESP_LOGW(TAG, "发送 camera_frame 失败");
        } else {
            ESP_LOGD(
                TAG,
                "已发送 camera_frame: stream_id=%s seq=%" PRIu32 " bytes=%u",
                s_current_camera_stream_id,
                s_camera_frame_seq - 1,
                (unsigned)fb->len
            );
        }
        esp_camera_fb_return(fb);
        fb = NULL;
        vTaskDelay(pdMS_TO_TICKS(s_camera_frame_interval_ms));
    }

    if (fb != NULL) {
        esp_camera_fb_return(fb);
    }
    if (s_camera_ws_client != NULL) {
        esp_websocket_client_stop(s_camera_ws_client);
        esp_websocket_client_destroy(s_camera_ws_client);
        s_camera_ws_client = NULL;
    }
    s_camera_stream_task_running = false;
    s_current_camera_stream_id[0] = '\0';
    s_camera_stream_ws_uri[0] = '\0';
    s_camera_frame_seq = 0;
    s_camera_stream_task_handle = NULL;
    vTaskDelete(NULL);
}

static void start_camera_stream(const char *stream_id, const char *target_ws_uri, int frame_interval_ms)
{
    esp_websocket_client_config_t websocket_config = {0};

    if (stream_id == NULL || stream_id[0] == '\0' || target_ws_uri == NULL || target_ws_uri[0] == '\0') {
        ESP_LOGW(TAG, "相机流启动参数不完整，已忽略");
        return;
    }
    if (!s_camera_initialized) {
        ESP_LOGW(TAG, "相机未初始化，无法启动持续视频流");
        return;
    }
    if (s_camera_capture_busy) {
        ESP_LOGW(TAG, "当前相机正被单次抓拍占用，无法启动持续视频流");
        return;
    }
    if (s_camera_stream_task_running) {
        ESP_LOGW(TAG, "当前已有持续视频流任务在运行，已忽略新的启动请求");
        return;
    }

    strlcpy(s_current_camera_stream_id, stream_id, sizeof(s_current_camera_stream_id));
    strlcpy(s_camera_stream_ws_uri, target_ws_uri, sizeof(s_camera_stream_ws_uri));
    s_camera_frame_interval_ms = frame_interval_ms > 0 ? frame_interval_ms : 500;
    s_camera_frame_seq = 0;

    websocket_config.uri = s_camera_stream_ws_uri;
    websocket_config.buffer_size = 16384;
    websocket_config.network_timeout_ms = 5000;
    websocket_config.task_stack = 8192;

    s_camera_ws_client = esp_websocket_client_init(&websocket_config);
    if (s_camera_ws_client == NULL) {
        ESP_LOGE(TAG, "创建相机流客户端失败");
        s_current_camera_stream_id[0] = '\0';
        s_camera_stream_ws_uri[0] = '\0';
        return;
    }
    ESP_ERROR_CHECK(
        esp_websocket_register_events(
            s_camera_ws_client,
            WEBSOCKET_EVENT_ANY,
            camera_websocket_event_handler,
            NULL
        )
    );
    s_camera_stream_active = true;
    s_camera_stream_task_running = true;
    if (
        xTaskCreateWithCaps(
            camera_stream_task,
            "camera_stream_task",
            CAMERA_STREAM_TASK_STACK_SIZE,
            NULL,
            5,
            &s_camera_stream_task_handle,
            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
        ) != pdPASS
    ) {
        size_t free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        size_t largest_internal = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        size_t free_spiram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        size_t largest_spiram = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        s_camera_stream_active = false;
        s_camera_stream_task_running = false;
        s_camera_stream_task_handle = NULL;
        s_current_camera_stream_id[0] = '\0';
        s_camera_stream_ws_uri[0] = '\0';
        esp_websocket_client_destroy(s_camera_ws_client);
        s_camera_ws_client = NULL;
        ESP_LOGE(
            TAG,
            "创建 camera_stream_task 失败: stack=%d free_internal=%u largest_internal=%u free_spiram=%u largest_spiram=%u",
            CAMERA_STREAM_TASK_STACK_SIZE,
            (unsigned)free_internal,
            (unsigned)largest_internal,
            (unsigned)free_spiram,
            (unsigned)largest_spiram
        );
        return;
    }

    if (esp_websocket_client_start(s_camera_ws_client) != ESP_OK) {
        ESP_LOGE(TAG, "启动相机流客户端失败");
        s_camera_stream_active = false;
        s_camera_stream_task_running = false;
        s_camera_stream_task_handle = NULL;
        s_current_camera_stream_id[0] = '\0';
        s_camera_stream_ws_uri[0] = '\0';
        esp_websocket_client_destroy(s_camera_ws_client);
        s_camera_ws_client = NULL;
        return;
    }
}

static void stop_camera_stream(const char *stream_id)
{
    if (!s_camera_stream_task_running || !s_camera_stream_active) {
        return;
    }
    if (stream_id != NULL && stream_id[0] != '\0' && strcmp(stream_id, s_current_camera_stream_id) != 0) {
        ESP_LOGW(
            TAG,
            "停止相机流时 stream_id 不匹配，已忽略: expected=%s actual=%s",
            s_current_camera_stream_id,
            stream_id
        );
        return;
    }
    s_camera_stream_active = false;
    ESP_LOGI(TAG, "已请求停止相机流: stream_id=%s", s_current_camera_stream_id);
}

static void playback_stream_task(void *arg)
{
    int32_t *stereo_buffer = NULL;
    uint8_t wav_header[44];
    int header_read = 0;
    bool started_sent = false;
    bool interrupted = false;
    bool failed = false;
    char next_stream_id[64];
    uint64_t last_stream_data_ms = now_ms();
    uint64_t task_started_ms = now_ms();
    uint64_t request_started_ms = s_playback_request_started_ms;
    bool first_pcm_logged = false;
    const char *terminal_state = "completed";
    const char *terminal_reason = "stream_completed";
    esp_http_client_config_t config = {
        .url = s_stream_wav_url,
        .timeout_ms = PLAYBACK_HTTP_TIMEOUT_MS,
        .buffer_size = 2048,
    };
    esp_http_client_handle_t client = NULL;

    next_stream_id[0] = '\0';

    stereo_buffer = heap_caps_malloc(AUDIO_FRAME_SAMPLES * 2 * sizeof(int32_t), MALLOC_CAP_8BIT);
    if (stereo_buffer == NULL) {
        ESP_LOGE(TAG, "分配播放缓冲失败");
        goto cleanup;
    }

    client = esp_http_client_init(&config);
    if (client == NULL) {
        ESP_LOGE(TAG, "创建播放 HTTP 客户端失败");
        goto cleanup;
    }
    if (esp_http_client_open(client, 0) != ESP_OK) {
        ESP_LOGE(TAG, "打开播放流失败: %s", s_stream_wav_url);
        goto cleanup;
    }
    ESP_LOGI(
        TAG,
        "播放流 HTTP 已打开: stream_id=%s request_to_http_open_ms=%" PRIu64 " task_to_http_open_ms=%" PRIu64,
        s_current_playback_stream_id,
        request_started_ms > 0 ? now_ms() - request_started_ms : 0,
        now_ms() - task_started_ms
    );
    if (esp_http_client_fetch_headers(client) < 0) {
        ESP_LOGE(TAG, "获取播放流响应头失败");
        goto cleanup;
    }

    while (header_read < (int)sizeof(wav_header)) {
        if (s_playback_interrupt_requested) {
            interrupted = true;
            goto cleanup;
        }
        int read_size = esp_http_client_read(client, (char *)wav_header + header_read, sizeof(wav_header) - header_read);
        if (read_size <= 0) {
            if (s_playback_interrupt_requested) {
                interrupted = true;
                goto cleanup;
            }
            if (!esp_http_client_is_complete_data_received(client) &&
                (now_ms() - last_stream_data_ms) < PLAYBACK_STREAM_IDLE_TIMEOUT_MS) {
                vTaskDelay(pdMS_TO_TICKS(20));
                continue;
            }
            failed = true;
            terminal_state = "failed";
            terminal_reason = "wav_header_read_failed";
            ESP_LOGE(TAG, "读取 WAV 头失败");
            goto cleanup;
        }
        last_stream_data_ms = now_ms();
        header_read += read_size;
    }
    ESP_LOGI(
        TAG,
        "播放流 WAV 头已读取: stream_id=%s request_to_header_ms=%" PRIu64 " task_to_header_ms=%" PRIu64,
        s_current_playback_stream_id,
        request_started_ms > 0 ? now_ms() - request_started_ms : 0,
        now_ms() - task_started_ms
    );
    if (memcmp(wav_header, "RIFF", 4) != 0 || memcmp(wav_header + 8, "WAVE", 4) != 0) {
        failed = true;
        terminal_state = "failed";
        terminal_reason = "invalid_wav_header";
        ESP_LOGE(TAG, "播放流不是有效 WAV 头");
        goto cleanup;
    }

    for (;;) {
        uint8_t pcm_buffer[AUDIO_FRAME_BYTES];
        size_t pcm_filled = 0;

        if (s_playback_interrupt_requested) {
            interrupted = true;
            break;
        }

        while (pcm_filled < sizeof(pcm_buffer)) {
            if (s_playback_interrupt_requested) {
                interrupted = true;
                goto cleanup;
            }
            int read_size = esp_http_client_read(
                client,
                (char *)pcm_buffer + pcm_filled,
                sizeof(pcm_buffer) - pcm_filled
            );
            if (read_size < 0) {
                if (s_playback_interrupt_requested) {
                    interrupted = true;
                    goto cleanup;
                }
                if (!esp_http_client_is_complete_data_received(client) &&
                    (now_ms() - last_stream_data_ms) < PLAYBACK_STREAM_IDLE_TIMEOUT_MS) {
                    vTaskDelay(pdMS_TO_TICKS(20));
                    continue;
                }
                failed = true;
                terminal_state = "failed";
                terminal_reason = "stream_read_failed";
                ESP_LOGE(TAG, "读取播放流失败");
                goto cleanup;
            }
            if (read_size == 0) {
                if (!esp_http_client_is_complete_data_received(client) &&
                    (now_ms() - last_stream_data_ms) < PLAYBACK_STREAM_IDLE_TIMEOUT_MS) {
                    vTaskDelay(pdMS_TO_TICKS(20));
                    continue;
                }
                break;
            }
            last_stream_data_ms = now_ms();
            pcm_filled += (size_t)read_size;
            if (!first_pcm_logged) {
                first_pcm_logged = true;
                ESP_LOGI(
                    TAG,
                    "播放流收到首段 PCM: stream_id=%s bytes=%d request_to_first_pcm_ms=%" PRIu64 " task_to_first_pcm_ms=%" PRIu64,
                    s_current_playback_stream_id,
                    read_size,
                    request_started_ms > 0 ? now_ms() - request_started_ms : 0,
                    now_ms() - task_started_ms
                );
            }
        }
        if (pcm_filled == 0) {
            break;
        }
        if ((pcm_filled % 2U) != 0U) {
            pcm_filled -= 1;
        }
        if (pcm_filled == 0) {
            continue;
        }

        mono16_to_stereo32_msb((const int16_t *)pcm_buffer, pcm_filled / 2U, stereo_buffer, 0.8f);
        size_t bytes_to_write = (pcm_filled / 2U) * 2U * sizeof(int32_t);
        size_t written_total = 0;
        while (written_total < bytes_to_write) {
            if (s_playback_interrupt_requested) {
                interrupted = true;
                goto cleanup;
            }
            size_t written_size = 0;
            esp_err_t err = i2s_channel_write(
                s_spk_tx_chan,
                (const uint8_t *)stereo_buffer + written_total,
                bytes_to_write - written_total,
                &written_size,
                pdMS_TO_TICKS(1000)
            );
            if (err != ESP_OK) {
                failed = true;
                terminal_state = "failed";
                terminal_reason = "speaker_write_failed";
                ESP_LOGE(TAG, "扬声器写入失败: %s", esp_err_to_name(err));
                goto cleanup;
            }
            written_total += written_size;
        }

        if (!started_sent) {
            send_actuator_audio_state_message("actuator.audio.started", s_current_playback_stream_id, NULL, NULL);
            started_sent = true;
            ESP_LOGI(
                TAG,
                "播放流首段音频已写入扬声器: stream_id=%s request_to_speaker_ms=%" PRIu64 " task_to_speaker_ms=%" PRIu64 " pcm_bytes=%u",
                s_current_playback_stream_id,
                request_started_ms > 0 ? now_ms() - request_started_ms : 0,
                now_ms() - task_started_ms,
                (unsigned)pcm_filled
            );
        }
    }

cleanup:
    if (interrupted) {
        terminal_state = "interrupted";
        terminal_reason = "interrupt_requested";
    } else if (failed) {
        terminal_state = "failed";
    }
    if (client != NULL) {
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
    }
    drain_and_pause_speaker();
    heap_caps_free(stereo_buffer);
    if (s_current_playback_stream_id[0] != '\0') {
        send_actuator_audio_state_message(
            "actuator.audio.state",
            s_current_playback_stream_id,
            terminal_state,
            terminal_reason
        );
        send_actuator_audio_state_message("actuator.audio.finished", s_current_playback_stream_id, NULL, NULL);
    }
    clear_reply_wait_state();
    s_playback_active = false;
    s_wake_listening_enabled = s_registered && s_voice_session_opened && s_sr_ctx.initialized;
    s_playback_task_running = false;
    s_playback_interrupt_requested = false;
    s_playback_task_handle = NULL;
    s_current_playback_stream_id[0] = '\0';
    s_playback_request_started_ms = 0;
    if (s_realtime_semantic_dialog_enabled && s_continuous_dialog_active) {
        s_continuous_dialog_last_activity_ms = now_ms();
    }
    if (s_next_playback_stream_id[0] != '\0') {
        strlcpy(next_stream_id, s_next_playback_stream_id, sizeof(next_stream_id));
        s_next_playback_stream_id[0] = '\0';
    }
    {
        const char *resume_message = interrupted ? "播放已被打断，恢复待命监听" : "播放结束，恢复待命监听";
        ESP_LOGI(TAG, "%s", resume_message);
    }
    if (next_stream_id[0] != '\0') {
        start_playback_stream(next_stream_id);
    }
    vTaskDelete(NULL);
}

static void start_playback_stream(const char *stream_id)
{
    int32_t zero_buffer[AUDIO_FRAME_SAMPLES * 2] = {0};
    size_t preloaded_size = 0;

    if (stream_id == NULL || stream_id[0] == '\0') {
        ESP_LOGW(TAG, "playback stream_id 为空，忽略播放请求");
        return;
    }
    if (!build_stream_wav_url(stream_id, s_stream_wav_url, sizeof(s_stream_wav_url))) {
        ESP_LOGE(TAG, "构造播放流地址失败");
        return;
    }
    ESP_LOGI(TAG, "准备启动播放流: stream_id=%s url=%s", stream_id, s_stream_wav_url);
    if (s_playback_task_running) {
        if (s_playback_interrupt_requested) {
            strlcpy(s_next_playback_stream_id, stream_id, sizeof(s_next_playback_stream_id));
            ESP_LOGI(TAG, "当前播放正在被打断，已暂存下一条播放请求: stream_id=%s", stream_id);
        } else {
            ESP_LOGW(TAG, "当前播放任务仍在运行，忽略新的播放请求");
        }
        return;
    }
    if (s_spk_tx_chan == NULL) {
        ESP_LOGE(TAG, "扬声器通道未初始化，无法播放");
        return;
    }
    if (!s_speaker_channel_enabled) {
        esp_err_t preload_err = i2s_channel_preload_data(
            s_spk_tx_chan,
            zero_buffer,
            sizeof(zero_buffer),
            &preloaded_size
        );
        if (preload_err != ESP_OK) {
            ESP_LOGW(TAG, "预装扬声器静音帧失败: %s", esp_err_to_name(preload_err));
        }
        esp_err_t enable_err = i2s_channel_enable(s_spk_tx_chan);
        if (enable_err != ESP_OK) {
            ESP_LOGE(TAG, "恢复扬声器通道失败: %s", esp_err_to_name(enable_err));
            return;
        }
        s_speaker_channel_enabled = true;
    }

    strlcpy(s_current_playback_stream_id, stream_id, sizeof(s_current_playback_stream_id));
    s_next_playback_stream_id[0] = '\0';
    clear_reply_wait_state();
    s_playback_active = true;
    s_wake_listening_enabled = false;
    s_playback_task_running = true;
    s_playback_interrupt_requested = false;
    s_playback_request_started_ms = now_ms();
    if (xTaskCreate(playback_stream_task, "playback_stream_task", 8192, NULL, 5, &s_playback_task_handle) != pdPASS) {
        ESP_LOGE(TAG, "创建 playback_stream_task 失败");
        s_playback_active = false;
        s_wake_listening_enabled = s_registered && s_voice_session_opened && s_sr_ctx.initialized;
        s_playback_task_running = false;
        s_playback_interrupt_requested = false;
        s_current_playback_stream_id[0] = '\0';
        s_playback_request_started_ms = 0;
        s_playback_task_handle = NULL;
    }
}

/*
 * 功能：请求打断当前播放任务。
 * 主要逻辑：
 * 1. 校验当前是否存在可打断的播放流。
 * 2. 可选校验 stream_id，避免误打断已经切换过的播放。
 * 3. 仅设置中断标志，由播放任务自行完成清理和 finished 回报。
 * 参数：
 * 1. stream_id：期望打断的播放流编号；为空时表示打断当前活动流。
 * 返回值：无。
 * 异常情况：若当前没有活动播放，或 stream_id 与当前流不一致，则直接忽略。
 */
static void request_playback_interrupt(const char *stream_id)
{
    if (!s_playback_task_running || !s_playback_active) {
        ESP_LOGI(TAG, "当前没有活动播放任务，忽略打断请求");
        return;
    }
    if (stream_id != NULL &&
        stream_id[0] != '\0' &&
        strcmp(stream_id, s_current_playback_stream_id) != 0) {
        ESP_LOGW(
            TAG,
            "打断请求的 stream_id 与当前播放流不一致，已忽略: expected=%s actual=%s",
            s_current_playback_stream_id,
            stream_id
        );
        return;
    }

    s_playback_interrupt_requested = true;
    ESP_LOGI(TAG, "已请求打断当前播放: stream_id=%s", s_current_playback_stream_id);
}

static void sr_pipeline_task(void *arg)
{
    sr_runtime_ctx_t *ctx = (sr_runtime_ctx_t *)arg;
    segment_state_t segment = {0};
    pre_roll_frame_t pre_roll_frames[PRE_ROLL_FRAME_COUNT] = {0};
    size_t pre_roll_next_index = 0;
    size_t pre_roll_valid_count = 0;
    bool last_wake_active = false;
    int last_vad_state = -999;
    int last_wakeup_state = -999;
    uint32_t idle_fetch_count = 0;
    uint64_t last_idle_summary_ms = now_ms();
    uint64_t last_gate_log_ms = 0;
    uint64_t last_audio_reconnect_ms = 0;

    ESP_LOGD(
        TAG,
        "SR pipeline started, feed_chunksize=%d, feed_nch=%d, chunk_ms=%d",
        ctx->feed_chunksize,
        ctx->feed_nch,
        ctx->feed_chunk_ms
    );

    for (;;) {
        bool current_frame_flushed_from_pre_roll = false;
        bool has_session_id = s_current_session_id[0] != '\0';
        bool wake_active = s_registered &&
                           s_voice_session_opened &&
                           s_wake_listening_enabled &&
                           s_audio_ws_ready &&
                           !s_playback_active &&
                           has_session_id;
        uint64_t current_ms = now_ms();
        if (s_registered &&
            s_voice_session_opened &&
            !s_playback_active &&
            !s_audio_ws_ready &&
            (current_ms - last_audio_reconnect_ms) >= AUDIO_WS_RECONNECT_INTERVAL_MS) {
            last_audio_reconnect_ms = current_ms;
            ESP_LOGW(TAG, "音频上行未就绪，尝试重新建立连接");
            ensure_audio_transport_started();
        }
        if (!segment.segment_active && !s_playback_active && reply_wait_timed_out(current_ms)) {
            recover_wake_listening_after_reply_timeout(current_ms);
            last_wake_active = false;
            last_gate_log_ms = current_ms;
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }
        if (s_continuous_dialog_active &&
            !segment.segment_active &&
            !s_playback_active &&
            s_continuous_dialog_last_activity_ms > 0 &&
            (current_ms - s_continuous_dialog_last_activity_ms) >= CONTINUOUS_DIALOG_IDLE_TIMEOUT_MS) {
            deactivate_continuous_dialog("idle_timeout");
        }
        if (wake_active != last_wake_active) {
            log_wake_gate_state(
                s_registered,
                s_voice_session_opened,
                s_wake_listening_enabled,
                s_audio_ws_ready,
                s_playback_active,
                has_session_id
            );
            last_wake_active = wake_active;
            last_gate_log_ms = current_ms;
        } else if (!wake_active && (current_ms - last_gate_log_ms) >= WAKE_IDLE_SUMMARY_MS) {
            log_wake_gate_state(
                s_registered,
                s_voice_session_opened,
                s_wake_listening_enabled,
                s_audio_ws_ready,
                s_playback_active,
                has_session_id
            );
            last_gate_log_ms = current_ms;
        }
        if (!wake_active) {
            if (segment.segment_active) {
                ESP_LOGD(TAG, "voice session inactive, reset local speech segment");
                reset_segment_state(&segment);
            }
            reset_pre_roll(
                pre_roll_frames,
                PRE_ROLL_FRAME_COUNT,
                &pre_roll_next_index,
                &pre_roll_valid_count
            );
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        size_t bytes_read = 0;
        esp_err_t ret = i2s_channel_read(
            s_mic_rx_chan,
            ctx->feed_buffer,
            ctx->feed_buffer_size_bytes,
            &bytes_read,
            pdMS_TO_TICKS(1000)
        );
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "mic read failed: %s", esp_err_to_name(ret));
            continue;
        }
        if (bytes_read < ctx->feed_buffer_size_bytes) {
            memset((uint8_t *)ctx->feed_buffer + bytes_read, 0, ctx->feed_buffer_size_bytes - bytes_read);
        }

        ctx->afe_handle->feed(ctx->afe_data, ctx->feed_buffer);
        afe_fetch_result_t *res = ctx->afe_handle->fetch(ctx->afe_data);
        if (!res) {
            continue;
        }

        if (res->wakeup_state != last_wakeup_state || res->vad_state != last_vad_state) {
            log_sr_fetch_state(
                segment.segment_active ? "segment_active" : "idle",
                res->wakeup_state,
                res->vad_state,
                res->data_size,
                segment.segment_active,
                segment.got_speech,
                segment.tail_silence_ms,
                segment.elapsed_ms
            );
            last_wakeup_state = res->wakeup_state;
            last_vad_state = res->vad_state;
        }
        if (!segment.segment_active) {
            idle_fetch_count += 1U;
            if ((current_ms - last_idle_summary_ms) >= WAKE_IDLE_SUMMARY_MS) {
                ESP_LOGD(
                    TAG,
                    "待唤醒摘要: idle_fetch_count=%" PRIu32 " wakeup_state=%d vad_state=%d audio_ws_ready=%d",
                    idle_fetch_count,
                    res->wakeup_state,
                    res->vad_state,
                    s_audio_ws_ready
                );
                idle_fetch_count = 0;
                last_idle_summary_ms = current_ms;
            }
        }

        if (res->data && res->data_size > 0) {
            store_pre_roll_frame(
                pre_roll_frames,
                PRE_ROLL_FRAME_COUNT,
                &pre_roll_next_index,
                &pre_roll_valid_count,
                (const uint8_t *)res->data,
                (size_t)res->data_size
            );
        }

        bool start_by_wake_word = res->wakeup_state == WAKENET_DETECTED;
        bool start_by_continuous_vad = s_realtime_semantic_dialog_enabled &&
                                       s_continuous_dialog_active &&
                                       res->vad_state == VAD_SPEECH;

        if (!segment.segment_active && (start_by_wake_word || start_by_continuous_vad)) {
            segment.segment_active = true;
            segment.got_speech = false;
            segment.tail_silence_ms = 0;
            segment.elapsed_ms = 0;
            segment.segment_pcm_bytes = 0;
            segment.chunk_seq = 0;
            build_runtime_token("seg", segment.segment_id, sizeof(segment.segment_id));
            if (start_by_wake_word) {
                refresh_continuous_dialog_activity();
                ESP_LOGI(TAG, "WakeNet detected: segment_id=%s", segment.segment_id);
                play_wake_prompt_tone();
            } else {
                refresh_continuous_dialog_activity();
                ESP_LOGI(TAG, "连续对话 VAD 触发新语音段: segment_id=%s", segment.segment_id);
            }
            send_audio_segment_started_message(segment.segment_id);
            flush_pre_roll_frames(
                pre_roll_frames,
                PRE_ROLL_FRAME_COUNT,
                pre_roll_next_index,
                pre_roll_valid_count,
                &segment
            );
            current_frame_flushed_from_pre_roll = res->data && res->data_size > 0;
            reset_pre_roll(
                pre_roll_frames,
                PRE_ROLL_FRAME_COUNT,
                &pre_roll_next_index,
                &pre_roll_valid_count
            );
            idle_fetch_count = 0;
            last_idle_summary_ms = current_ms;
        }

        if (!segment.segment_active) {
            continue;
        }

        segment.elapsed_ms += ctx->feed_chunk_ms;
        if (!current_frame_flushed_from_pre_roll && res->data && res->data_size > 0) {
            if (send_audio_chunk_frame(
                s_current_stream_id,
                segment.segment_id,
                (const uint8_t *)res->data,
                (size_t)res->data_size,
                segment.chunk_seq,
                false
            )) {
                segment.segment_pcm_bytes += (uint32_t)res->data_size;
                segment.chunk_seq += 1;
            }
        }

        if (res->vad_state == VAD_SPEECH) {
            segment.got_speech = true;
            segment.tail_silence_ms = 0;
        } else if (segment.got_speech && res->vad_state == VAD_SILENCE) {
            segment.tail_silence_ms += ctx->feed_chunk_ms;
        }

        bool min_capture_reached = segment.elapsed_ms >= LOCAL_ENDPOINT_MIN_MS;
        bool endpoint_by_silence = min_capture_reached &&
                                   segment.got_speech &&
                                   (segment.tail_silence_ms >= LOCAL_ENDPOINT_TAIL_MS);
        bool endpoint_by_timeout = (segment.elapsed_ms >= LOCAL_ENDPOINT_MAX_MS);
        if (!endpoint_by_silence && !endpoint_by_timeout) {
            continue;
        }

        ESP_LOGI(
            TAG,
            "local segment closed (%s), segment_id=%s elapsed=%d ms tail_silence=%d ms pcm_bytes=%" PRIu32,
            endpoint_by_silence ? "tail_silence" : "timeout",
            segment.segment_id,
            segment.elapsed_ms,
            segment.tail_silence_ms,
            segment.segment_pcm_bytes
        );
        send_audio_segment_finished_message(
            segment.segment_id,
            segment.elapsed_ms,
            segment.segment_pcm_bytes,
            endpoint_by_silence ? "endpoint_detected" : "max_capture"
        );
        refresh_continuous_dialog_activity();
        begin_reply_wait_state();
        s_wake_listening_enabled = false;
        reset_segment_state(&segment);
    }
}

static void init_speech_runtime(void)
{
    size_t psram_size;
    srmodel_list_t *models = NULL;
    afe_config_t *afe_cfg = NULL;
    char *wn_name = NULL;
    BaseType_t task_ret;

    memset(&s_sr_ctx, 0, sizeof(s_sr_ctx));

    psram_size = esp_psram_get_size();
    ESP_LOGI(TAG, "Detected PSRAM size: %u bytes", (unsigned)psram_size);
    if (psram_size == 0) {
        ESP_LOGW(TAG, "No PSRAM detected; WakeNet runtime disabled");
        return;
    }

    if (init_mic_i2s() != ESP_OK) {
        ESP_LOGE(TAG, "init_mic_i2s failed, WakeNet runtime disabled");
        return;
    }
    if (init_speaker_i2s() != ESP_OK) {
        ESP_LOGE(TAG, "init_speaker_i2s failed, playback runtime disabled");
        return;
    }

    models = esp_srmodel_init("model");
    if (!models) {
        ESP_LOGE(TAG, "esp_srmodel_init(\"model\") failed, check model partition/config");
        return;
    }

    afe_cfg = afe_config_init(AFE_INPUT_FORMAT, models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
    if (!afe_cfg) {
        ESP_LOGE(TAG, "afe_config_init failed");
        return;
    }

    afe_cfg->wakenet_init = true;
    afe_cfg->vad_init = true;
    afe_cfg->aec_init = false;

    wn_name = (char *)preferred_wakenet_model_name(models);
    if (!wn_name) {
        ESP_LOGE(TAG, "No WakeNet model found; enable one in sdkconfig.defaults");
        return;
    }

    afe_cfg->wakenet_model_name = wn_name;
    strlcpy(s_sr_ctx.wake_model_name, wn_name, sizeof(s_sr_ctx.wake_model_name));
    ESP_LOGI(TAG, "WakeNet model selected: %s", s_sr_ctx.wake_model_name);

    s_sr_ctx.afe_handle = esp_afe_handle_from_config(afe_cfg);
    if (!s_sr_ctx.afe_handle) {
        ESP_LOGE(TAG, "esp_afe_handle_from_config failed");
        return;
    }

    s_sr_ctx.afe_data = s_sr_ctx.afe_handle->create_from_config(afe_cfg);
    if (!s_sr_ctx.afe_data) {
        ESP_LOGE(TAG, "afe create_from_config failed");
        return;
    }

    s_sr_ctx.feed_chunksize = s_sr_ctx.afe_handle->get_feed_chunksize(s_sr_ctx.afe_data);
    s_sr_ctx.feed_nch = s_sr_ctx.afe_handle->get_feed_channel_num(s_sr_ctx.afe_data);
    s_sr_ctx.feed_chunk_ms = (s_sr_ctx.feed_chunksize * 1000) / SR_SAMPLE_RATE_HZ;
    if (s_sr_ctx.feed_chunk_ms <= 0) {
        s_sr_ctx.feed_chunk_ms = 1;
    }
    s_sr_ctx.feed_buffer_size_bytes = s_sr_ctx.feed_chunksize * s_sr_ctx.feed_nch * sizeof(int16_t);
    s_sr_ctx.feed_buffer = heap_caps_calloc(
        1,
        s_sr_ctx.feed_buffer_size_bytes,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
    );
    if (!s_sr_ctx.feed_buffer) {
        s_sr_ctx.feed_buffer = heap_caps_calloc(
            1,
            s_sr_ctx.feed_buffer_size_bytes,
            MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT
        );
    }
    if (!s_sr_ctx.feed_buffer) {
        ESP_LOGE(TAG, "feed buffer alloc failed");
        return;
    }

    task_ret = xTaskCreatePinnedToCore(
        sr_pipeline_task,
        "sr_pipeline_task",
        8 * 1024,
        &s_sr_ctx,
        5,
        NULL,
        1
    );
    if (task_ret != pdPASS) {
        ESP_LOGE(TAG, "failed to create sr_pipeline_task");
        return;
    }

    s_sr_task_started = true;
    s_sr_ctx.initialized = true;
    if (s_registered && s_voice_session_opened && !s_playback_active) {
        s_wake_listening_enabled = true;
        ESP_LOGI(TAG, "WakeNet 初始化完成后已补开唤醒监听: session_id=%s", s_current_session_id);
    }
    ESP_LOGI(TAG, "WakeNet runtime ready; waiting for voice.session.open");
}

static void handle_control_message(const char *data, int data_len)
{
    cJSON *root = NULL;
    const cJSON *name = NULL;
    const cJSON *payload = NULL;
    const cJSON *session_id = NULL;
    const cJSON *stream_id = NULL;
    const cJSON *request_id = NULL;
    const cJSON *reason = NULL;

    char *json_text = calloc((size_t)data_len + 1U, sizeof(char));
    if (json_text == NULL) {
        ESP_LOGE(TAG, "分配消息缓冲失败");
        return;
    }
    memcpy(json_text, data, (size_t)data_len);

    root = cJSON_Parse(json_text);
    if (root == NULL) {
        ESP_LOGW(TAG, "收到无法解析的控制消息");
        goto cleanup;
    }

    name = cJSON_GetObjectItemCaseSensitive(root, "name");
    payload = cJSON_GetObjectItemCaseSensitive(root, "payload");
    session_id = cJSON_GetObjectItemCaseSensitive(root, "session_id");
    stream_id = cJSON_GetObjectItemCaseSensitive(root, "stream_id");
    request_id = payload != NULL ? cJSON_GetObjectItemCaseSensitive(payload, "request_id") : NULL;
    reason = payload != NULL ? cJSON_GetObjectItemCaseSensitive(payload, "reason") : NULL;
    if (!cJSON_IsString(name) || name->valuestring == NULL) {
        ESP_LOGW(TAG, "控制消息缺少 name");
        goto cleanup;
    }

    if (strcmp(name->valuestring, "device.registered") == 0) {
        const cJSON *heartbeat = payload != NULL
            ? cJSON_GetObjectItemCaseSensitive(payload, "heartbeat_interval_ms")
            : NULL;
        if (cJSON_IsNumber(heartbeat) && heartbeat->valuedouble > 0) {
            s_runtime_config.heartbeat_interval_ms = (uint32_t)heartbeat->valuedouble;
        }
        s_registered = true;
        ESP_LOGI(
            TAG,
            "注册成功: device_id=%s heartbeat_interval_ms=%" PRIu32,
            s_runtime_config.device_id,
            s_runtime_config.heartbeat_interval_ms
        );
        goto cleanup;
    }

    if (strcmp(name->valuestring, "device.register.failed") == 0) {
        const cJSON *error = payload != NULL ? cJSON_GetObjectItemCaseSensitive(payload, "error") : NULL;
        const cJSON *message = error != NULL ? cJSON_GetObjectItemCaseSensitive(error, "message") : NULL;
        reset_control_session_state();
        ESP_LOGE(
            TAG,
            "注册失败: %s",
            cJSON_IsString(message) ? message->valuestring : "unknown"
        );
        goto cleanup;
    }

    if (strcmp(name->valuestring, "voice.session.open") == 0) {
        s_realtime_semantic_dialog_enabled = false;
        deactivate_continuous_dialog("classic_voice_session_open");
        if (cJSON_IsString(session_id) && session_id->valuestring != NULL) {
            strlcpy(s_current_session_id, session_id->valuestring, sizeof(s_current_session_id));
        } else {
            s_current_session_id[0] = '\0';
        }
        build_runtime_token("stream", s_current_stream_id, sizeof(s_current_stream_id));
        s_voice_session_opened = false;
        clear_reply_wait_state();
        ESP_LOGI(TAG, "收到 voice.session.open: session_id=%s", s_current_session_id);
        send_voice_session_opened_message(s_current_session_id);
        s_voice_session_opened = true;
        ensure_audio_transport_started();
        s_wake_listening_enabled = s_sr_ctx.initialized;
        if (!s_sr_ctx.initialized) {
            ESP_LOGW(TAG, "WakeNet runtime not ready; wake word status reporting disabled");
        } else {
            ESP_LOGI(TAG, "WakeNet listening enabled for session_id=%s", s_current_session_id);
        }
        goto cleanup;
    }

    if (strcmp(name->valuestring, "voice.realtime.session.open") == 0) {
        s_realtime_semantic_dialog_enabled = payload_requests_omni_semantic_dialog(payload);
        deactivate_continuous_dialog("realtime_session_open");
        if (cJSON_IsString(session_id) && session_id->valuestring != NULL) {
            strlcpy(s_current_session_id, session_id->valuestring, sizeof(s_current_session_id));
        } else {
            s_current_session_id[0] = '\0';
        }
        build_runtime_token("stream", s_current_stream_id, sizeof(s_current_stream_id));
        s_voice_session_opened = false;
        clear_reply_wait_state();
        ESP_LOGI(
            TAG,
            "收到 voice.realtime.session.open，当前固件降级为半双工: session_id=%s semantic_continuous=%d",
            s_current_session_id,
            s_realtime_semantic_dialog_enabled
        );
        send_realtime_session_opened_message(s_current_session_id);
        s_voice_session_opened = true;
        ensure_audio_transport_started();
        s_wake_listening_enabled = s_sr_ctx.initialized;
        if (!s_sr_ctx.initialized) {
            ESP_LOGW(TAG, "WakeNet runtime not ready; wake word status reporting disabled");
        } else {
            ESP_LOGI(TAG, "WakeNet listening enabled for realtime-degraded session_id=%s", s_current_session_id);
        }
        goto cleanup;
    }

    if (strcmp(name->valuestring, "actuator.audio.play") == 0) {
        const cJSON *payload_stream_id = payload != NULL
            ? cJSON_GetObjectItemCaseSensitive(payload, "stream_id")
            : NULL;
        const char *play_stream_id = NULL;

        if (cJSON_IsString(stream_id) && stream_id->valuestring != NULL) {
            play_stream_id = stream_id->valuestring;
        } else if (cJSON_IsString(payload_stream_id) && payload_stream_id->valuestring != NULL) {
            play_stream_id = payload_stream_id->valuestring;
        }

        ESP_LOGD(TAG, "收到 actuator.audio.play: stream_id=%s", play_stream_id != NULL ? play_stream_id : "<none>");
        start_playback_stream(play_stream_id);
        goto cleanup;
    }

    if (strcmp(name->valuestring, "actuator.audio.interrupt") == 0) {
        const cJSON *payload_stream_id = payload != NULL
            ? cJSON_GetObjectItemCaseSensitive(payload, "stream_id")
            : NULL;
        const char *interrupt_stream_id = NULL;

        if (cJSON_IsString(stream_id) && stream_id->valuestring != NULL) {
            interrupt_stream_id = stream_id->valuestring;
        } else if (cJSON_IsString(payload_stream_id) && payload_stream_id->valuestring != NULL) {
            interrupt_stream_id = payload_stream_id->valuestring;
        }

        ESP_LOGI(
            TAG,
            "收到 actuator.audio.interrupt: stream_id=%s",
            interrupt_stream_id != NULL ? interrupt_stream_id : "<current>"
        );
        request_playback_interrupt(interrupt_stream_id);
        goto cleanup;
    }

    if (strcmp(name->valuestring, "sensor.camera.capture") == 0) {
        camera_capture_task_arg_t *task_arg = NULL;
        BaseType_t task_ret;

        if (!cJSON_IsString(session_id) || session_id->valuestring == NULL) {
            ESP_LOGW(TAG, "sensor.camera.capture 缺少 session_id");
            goto cleanup;
        }
        if (!cJSON_IsString(request_id) || request_id->valuestring == NULL) {
            ESP_LOGW(TAG, "sensor.camera.capture 缺少 request_id");
            goto cleanup;
        }
        if (s_camera_stream_task_running || s_camera_stream_active) {
            send_camera_captured_message(
                session_id->valuestring,
                request_id->valuestring,
                false,
                NULL,
                NULL,
                0,
                0,
                NULL,
                "camera stream is already running"
            );
            goto cleanup;
        }
        if (s_camera_capture_busy) {
            send_camera_captured_message(
                session_id->valuestring,
                request_id->valuestring,
                false,
                NULL,
                NULL,
                0,
                0,
                NULL,
                "camera is already occupied by another task"
            );
            goto cleanup;
        }

        task_arg = calloc(1, sizeof(camera_capture_task_arg_t));
        if (task_arg == NULL) {
            send_camera_captured_message(
                session_id->valuestring,
                request_id->valuestring,
                false,
                NULL,
                NULL,
                0,
                0,
                NULL,
                "设备内存不足，无法启动抓拍任务"
            );
            goto cleanup;
        }
        strlcpy(task_arg->request_id, request_id->valuestring, sizeof(task_arg->request_id));
        strlcpy(task_arg->session_id, session_id->valuestring, sizeof(task_arg->session_id));
        if (cJSON_IsString(reason) && reason->valuestring != NULL) {
            strlcpy(task_arg->reason, reason->valuestring, sizeof(task_arg->reason));
        } else {
            strlcpy(task_arg->reason, "agent_requested", sizeof(task_arg->reason));
        }

        s_camera_capture_busy = true;
        task_ret = xTaskCreateWithCaps(
            camera_capture_task,
            "camera_capture_task",
            CAMERA_CAPTURE_TASK_STACK_SIZE,
            task_arg,
            5,
            NULL,
            MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT
        );
        if (task_ret != pdPASS) {
            size_t free_internal = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
            size_t largest_internal = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
            size_t free_spiram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            size_t largest_spiram = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
            s_camera_capture_busy = false;
            free(task_arg);
            ESP_LOGE(
                TAG,
                "创建设备抓拍任务失败: stack=%d free_internal=%u largest_internal=%u free_spiram=%u largest_spiram=%u",
                CAMERA_CAPTURE_TASK_STACK_SIZE,
                (unsigned)free_internal,
                (unsigned)largest_internal,
                (unsigned)free_spiram,
                (unsigned)largest_spiram
            );
            send_camera_captured_message(
                session_id->valuestring,
                request_id->valuestring,
                false,
                NULL,
                NULL,
                0,
                0,
                NULL,
                "创建设备抓拍任务失败"
            );
        }
        goto cleanup;
    }

    if (strcmp(name->valuestring, "sensor.camera.stream.start") == 0) {
        const cJSON *payload_stream_id = payload != NULL
            ? cJSON_GetObjectItemCaseSensitive(payload, "stream_id")
            : NULL;
        const cJSON *payload_target_ws_uri = payload != NULL
            ? cJSON_GetObjectItemCaseSensitive(payload, "target_ws_uri")
            : NULL;
        const cJSON *payload_frame_interval_ms = payload != NULL
            ? cJSON_GetObjectItemCaseSensitive(payload, "frame_interval_ms")
            : NULL;
        int frame_interval_ms = 500;

        if (!cJSON_IsString(payload_stream_id) || payload_stream_id->valuestring == NULL) {
            ESP_LOGW(TAG, "sensor.camera.stream.start 缺少 stream_id");
            goto cleanup;
        }
        if (!cJSON_IsString(payload_target_ws_uri) || payload_target_ws_uri->valuestring == NULL) {
            ESP_LOGW(TAG, "sensor.camera.stream.start 缺少 target_ws_uri");
            goto cleanup;
        }
        if (cJSON_IsNumber(payload_frame_interval_ms)) {
            frame_interval_ms = payload_frame_interval_ms->valueint;
        }
        ESP_LOGI(
            TAG,
            "收到 sensor.camera.stream.start: stream_id=%s target_ws_uri=%s frame_interval_ms=%d",
            payload_stream_id->valuestring,
            payload_target_ws_uri->valuestring,
            frame_interval_ms
        );
        start_camera_stream(payload_stream_id->valuestring, payload_target_ws_uri->valuestring, frame_interval_ms);
        goto cleanup;
    }

    if (strcmp(name->valuestring, "sensor.camera.stream.stop") == 0) {
        const cJSON *payload_stream_id = payload != NULL
            ? cJSON_GetObjectItemCaseSensitive(payload, "stream_id")
            : NULL;
        const char *stop_stream_id = NULL;

        if (cJSON_IsString(payload_stream_id) && payload_stream_id->valuestring != NULL) {
            stop_stream_id = payload_stream_id->valuestring;
        }
        ESP_LOGI(
            TAG,
            "收到 sensor.camera.stream.stop: stream_id=%s",
            stop_stream_id != NULL ? stop_stream_id : "<current>"
        );
        stop_camera_stream(stop_stream_id);
        goto cleanup;
    }

    ESP_LOGD(TAG, "收到未处理控制消息: %s", name->valuestring);

cleanup:
    cJSON_Delete(root);
    free(json_text);
}

static void websocket_event_handler(
    void *handler_args,
    esp_event_base_t base,
    int32_t event_id,
    void *event_data
)
{
    (void)handler_args;
    (void)base;

    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;

    if (event_id == WEBSOCKET_EVENT_CONNECTED) {
        ESP_LOGI(TAG, "控制连接已建立");
        reset_control_session_state();
        send_register_message();
        return;
    }

    if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
        ESP_LOGW(TAG, "控制连接已断开");
        reset_control_session_state();
        return;
    }

    if (event_id == WEBSOCKET_EVENT_DATA && data->op_code == 0x1 && data->data_ptr != NULL) {
        handle_control_message((const char *)data->data_ptr, data->data_len);
        return;
    }

    if (event_id == WEBSOCKET_EVENT_ERROR) {
        ESP_LOGE(TAG, "控制连接发生错误");
        reset_control_session_state();
    }
}

static void start_control_connection(void)
{
    esp_websocket_client_config_t websocket_config = {
        .uri = s_runtime_config.server_ws_uri,
        .buffer_size = 16384,
        .network_timeout_ms = 5000,
        .reconnect_timeout_ms = 10000,
        .task_stack = 8192,
    };

    s_ws_client = esp_websocket_client_init(&websocket_config);
    if (s_ws_client == NULL) {
        ESP_LOGE(TAG, "创建控制连接客户端失败");
        return;
    }

    ESP_ERROR_CHECK(
        esp_websocket_register_events(
            s_ws_client,
            WEBSOCKET_EVENT_ANY,
            websocket_event_handler,
            NULL
        )
    );
    ESP_ERROR_CHECK(esp_websocket_client_start(s_ws_client));
    s_control_transport_started = true;
}

static void ensure_control_transport_started(void)
{
    if (s_control_transport_started) {
        if (s_ws_client != NULL && !esp_websocket_client_is_connected(s_ws_client)) {
            ESP_LOGW(TAG, "控制连接未就绪，尝试重新建立连接");
            esp_websocket_client_stop(s_ws_client);
            if (esp_websocket_client_start(s_ws_client) != ESP_OK) {
                ESP_LOGW(TAG, "重新启动控制连接失败");
            }
        }
        return;
    }

    start_control_connection();
}

static void heartbeat_task(void *arg)
{
    (void)arg;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(s_runtime_config.heartbeat_interval_ms));
        if (s_ws_client == NULL || !esp_websocket_client_is_connected(s_ws_client)) {
            ensure_control_transport_started();
            continue;
        }
        if (s_registered && s_ws_client != NULL && esp_websocket_client_is_connected(s_ws_client)) {
            send_heartbeat_message();
        }
    }
}

void app_main(void)
{
    ESP_EARLY_LOGI(TAG, "app_main entered");
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_EARLY_LOGW(TAG, "nvs 需要擦除后重新初始化: %s", esp_err_to_name(ret));
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    ESP_EARLY_LOGI(TAG, "nvs 初始化完成");

    ESP_LOGI(TAG, "glass runtime bootstrapping (Phase C)");
    log_runtime_config();
    ESP_EARLY_LOGI(TAG, "运行时配置已输出");

    if (!init_wifi()) {
        return;
    }
    ESP_EARLY_LOGI(TAG, "WiFi 初始化完成");

    start_control_connection();
    ESP_EARLY_LOGI(TAG, "控制连接初始化完成");
    s_camera_initialized = init_camera();
    if (!s_camera_initialized) {
        ESP_LOGW(TAG, "摄像头初始化失败，当前仅保留语音链路");
    }
    init_speech_runtime();
    ESP_EARLY_LOGI(TAG, "语音运行时初始化完成");

    BaseType_t task_ret = xTaskCreate(
        heartbeat_task,
        "glass_heartbeat_task",
        4096,
        NULL,
        5,
        NULL
    );
    if (task_ret != pdPASS) {
        ESP_LOGE(TAG, "failed to create glass_heartbeat_task");
        return;
    }

    ESP_LOGI(TAG, "glass runtime entered Phase C main loop");
}
