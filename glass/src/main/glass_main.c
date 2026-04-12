#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "driver/i2s_pdm.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_psram.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAILED_BIT BIT1
#define WIFI_PROFILE_COUNT 2
#define WIFI_RETRY_PER_PROFILE 8
#define CONTROL_TARGET_DEVICE_ID "server-main"

#define MIC_PDM_CLK_GPIO GPIO_NUM_42
#define MIC_PDM_DATA_GPIO GPIO_NUM_41
#define SR_SAMPLE_RATE_HZ 16000
#define AFE_INPUT_FORMAT "M"
#define LOCAL_ENDPOINT_TAIL_MS 900
#define LOCAL_ENDPOINT_MAX_MS 8000

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
    char segment_id[64];
} segment_state_t;

static const char *TAG = "glass-main";
static EventGroupHandle_t s_wifi_event_group;
static esp_websocket_client_handle_t s_ws_client;
static esp_event_handler_instance_t s_wifi_event_instance;
static esp_event_handler_instance_t s_ip_event_instance;
static i2s_chan_handle_t s_mic_rx_chan;
static int s_active_wifi_profile = 0;
static int s_wifi_retry_count = 0;
static uint32_t s_message_sequence = 0;
static bool s_registered = false;
static bool s_voice_session_opened = false;
static bool s_wake_listening_enabled = false;
static bool s_sr_task_started = false;
static char s_current_session_id[64];
static char s_current_stream_id[64];
static sr_runtime_ctx_t s_sr_ctx;

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

static bool wifi_profile_available(int index)
{
    return index >= 0 &&
           index < WIFI_PROFILE_COUNT &&
           s_wifi_profiles[index].ssid[0] != '\0';
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
    } else {
        ESP_LOGI(TAG, "已发送控制消息: %s", name);
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

static bool try_switch_wifi_profile(void)
{
    int next = s_active_wifi_profile + 1;
    while (next < WIFI_PROFILE_COUNT) {
        if (!wifi_profile_available(next)) {
            next += 1;
            continue;
        }

        s_active_wifi_profile = next;
        s_wifi_retry_count = 0;
        ESP_LOGW(TAG, "主 WiFi 连接失败，切换到兜底 WiFi: %s", s_wifi_profiles[next].ssid);
        ESP_ERROR_CHECK(apply_wifi_profile(next));
        ESP_ERROR_CHECK(esp_wifi_connect());
        return true;
    }
    return false;
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
        ESP_LOGI(TAG, "开始连接 WiFi: %s", s_wifi_profiles[s_active_wifi_profile].ssid);
        ESP_ERROR_CHECK(esp_wifi_connect());
        return;
    }

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        s_registered = false;
        s_voice_session_opened = false;
        s_wake_listening_enabled = false;
        s_current_session_id[0] = '\0';
        s_current_stream_id[0] = '\0';

        if (s_wifi_retry_count < WIFI_RETRY_PER_PROFILE) {
            s_wifi_retry_count += 1;
            ESP_LOGW(
                TAG,
                "WiFi 断开，继续重试: ssid=%s retry=%d",
                s_wifi_profiles[s_active_wifi_profile].ssid,
                s_wifi_retry_count
            );
            ESP_ERROR_CHECK(esp_wifi_connect());
            return;
        }

        if (!try_switch_wifi_profile()) {
            ESP_LOGE(TAG, "WiFi 连接失败，所有配置均已尝试");
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAILED_BIT);
        }
        return;
    }

    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        s_wifi_retry_count = 0;
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

    if (!wifi_profile_available(0)) {
        ESP_LOGE(TAG, "主 WiFi 名称为空，请先在本地配置文件中设置");
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
        WIFI_CONNECTED_BIT | WIFI_FAILED_BIT,
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

static void reset_segment_state(segment_state_t *state)
{
    state->segment_active = false;
    state->got_speech = false;
    state->tail_silence_ms = 0;
    state->elapsed_ms = 0;
    state->segment_pcm_bytes = 0;
    state->segment_id[0] = '\0';
}

static void sr_pipeline_task(void *arg)
{
    sr_runtime_ctx_t *ctx = (sr_runtime_ctx_t *)arg;
    segment_state_t segment = {0};

    ESP_LOGI(
        TAG,
        "SR pipeline started, feed_chunksize=%d, feed_nch=%d, chunk_ms=%d",
        ctx->feed_chunksize,
        ctx->feed_nch,
        ctx->feed_chunk_ms
    );

    for (;;) {
        bool wake_active = s_registered &&
                           s_voice_session_opened &&
                           s_wake_listening_enabled &&
                           s_current_session_id[0] != '\0';
        if (!wake_active) {
            if (segment.segment_active) {
                ESP_LOGI(TAG, "voice session inactive, reset local speech segment");
                reset_segment_state(&segment);
            }
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

        if (!segment.segment_active && res->wakeup_state == WAKENET_DETECTED) {
            segment.segment_active = true;
            segment.got_speech = false;
            segment.tail_silence_ms = 0;
            segment.elapsed_ms = 0;
            segment.segment_pcm_bytes = 0;
            build_runtime_token("seg", segment.segment_id, sizeof(segment.segment_id));
            ESP_LOGI(TAG, "WakeNet detected: segment_id=%s", segment.segment_id);
            send_audio_segment_started_message(segment.segment_id);
        }

        if (!segment.segment_active) {
            continue;
        }

        segment.elapsed_ms += ctx->feed_chunk_ms;
        if (res->data && res->data_size > 0) {
            segment.segment_pcm_bytes += (uint32_t)res->data_size;
        }

        if (res->vad_state == VAD_SPEECH) {
            segment.got_speech = true;
            segment.tail_silence_ms = 0;
        } else if (segment.got_speech && res->vad_state == VAD_SILENCE) {
            segment.tail_silence_ms += ctx->feed_chunk_ms;
        }

        bool endpoint_by_silence = segment.got_speech && (segment.tail_silence_ms >= LOCAL_ENDPOINT_TAIL_MS);
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

    wn_name = esp_srmodel_filter(models, ESP_WN_PREFIX, NULL);
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
    ESP_LOGI(TAG, "WakeNet runtime ready; waiting for voice.session.open");
}

static void handle_control_message(const char *data, int data_len)
{
    cJSON *root = NULL;
    const cJSON *name = NULL;
    const cJSON *payload = NULL;
    const cJSON *session_id = NULL;

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
        s_registered = false;
        s_voice_session_opened = false;
        s_wake_listening_enabled = false;
        s_current_session_id[0] = '\0';
        s_current_stream_id[0] = '\0';
        ESP_LOGE(
            TAG,
            "注册失败: %s",
            cJSON_IsString(message) ? message->valuestring : "unknown"
        );
        goto cleanup;
    }

    if (strcmp(name->valuestring, "voice.session.open") == 0) {
        if (cJSON_IsString(session_id) && session_id->valuestring != NULL) {
            strlcpy(s_current_session_id, session_id->valuestring, sizeof(s_current_session_id));
        } else {
            s_current_session_id[0] = '\0';
        }
        build_runtime_token("stream", s_current_stream_id, sizeof(s_current_stream_id));
        s_voice_session_opened = false;
        ESP_LOGI(TAG, "收到 voice.session.open: session_id=%s", s_current_session_id);
        send_voice_session_opened_message(s_current_session_id);
        s_voice_session_opened = true;
        s_wake_listening_enabled = s_sr_ctx.initialized;
        if (!s_sr_ctx.initialized) {
            ESP_LOGW(TAG, "WakeNet runtime not ready; wake word status reporting disabled");
        } else {
            ESP_LOGI(TAG, "WakeNet listening enabled for session_id=%s", s_current_session_id);
        }
        goto cleanup;
    }

    ESP_LOGI(TAG, "收到未处理控制消息: %s", name->valuestring);

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
        s_registered = false;
        s_voice_session_opened = false;
        s_wake_listening_enabled = false;
        s_current_session_id[0] = '\0';
        s_current_stream_id[0] = '\0';
        send_register_message();
        return;
    }

    if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
        ESP_LOGW(TAG, "控制连接已断开");
        s_registered = false;
        s_voice_session_opened = false;
        s_wake_listening_enabled = false;
        s_current_session_id[0] = '\0';
        s_current_stream_id[0] = '\0';
        return;
    }

    if (event_id == WEBSOCKET_EVENT_DATA && data->op_code == 0x1 && data->data_ptr != NULL) {
        handle_control_message((const char *)data->data_ptr, data->data_len);
        return;
    }

    if (event_id == WEBSOCKET_EVENT_ERROR) {
        ESP_LOGE(TAG, "控制连接发生错误");
    }
}

static void start_control_connection(void)
{
    esp_websocket_client_config_t websocket_config = {
        .uri = s_runtime_config.server_ws_uri,
        .buffer_size = 2048,
        .network_timeout_ms = 5000,
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
}

static void heartbeat_task(void *arg)
{
    (void)arg;
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(s_runtime_config.heartbeat_interval_ms));
        if (s_registered && s_ws_client != NULL && esp_websocket_client_is_connected(s_ws_client)) {
            send_heartbeat_message();
        }
    }
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_LOGI(TAG, "glass runtime bootstrapping (Phase B)");
    log_runtime_config();

    if (!init_wifi()) {
        return;
    }

    start_control_connection();
    init_speech_runtime();

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

    ESP_LOGI(TAG, "glass runtime entered Phase B main loop");
}
