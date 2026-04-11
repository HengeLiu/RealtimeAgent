#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>

#include "driver/gpio.h"
#include "driver/i2s_pdm.h"
#include "driver/i2s_std.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

// XIAO ESP32S3 Sense pin map (from docs pin table)
#define MIC_PDM_CLK_GPIO GPIO_NUM_42
#define MIC_PDM_DATA_GPIO GPIO_NUM_41
#define SPK_BCLK_GPIO GPIO_NUM_7
#define SPK_LRCK_GPIO GPIO_NUM_8
#define SPK_DIN_GPIO GPIO_NUM_9

#define SR_SAMPLE_RATE_HZ 16000
#define AFE_INPUT_FORMAT "M"

#define LOCAL_ENDPOINT_TAIL_MS 900
#define LOCAL_ENDPOINT_MAX_MS 8000

#define DEMO_PLAYBACK_FREQ_HZ 880
#define DEMO_PLAYBACK_DURATION_MS 1200

static const char *TAG = "esp32s3_sr_spike";

static i2s_chan_handle_t s_mic_rx_chan;
static i2s_chan_handle_t s_spk_tx_chan;
static volatile bool s_playback_gate_on;

typedef struct {
    esp_afe_sr_iface_t *afe_handle;
    esp_afe_sr_data_t *afe_data;
    int16_t *feed_buffer;
    size_t feed_buffer_size_bytes;
    int feed_chunksize;
    int feed_nch;
    int feed_chunk_ms;
} sr_runtime_ctx_t;

typedef struct {
    bool segment_active;
    bool got_speech;
    int tail_silence_ms;
    int elapsed_ms;
    uint32_t segment_pcm_bytes;
} segment_state_t;

static void set_playback_gate(esp_afe_sr_iface_t *afe_handle, esp_afe_sr_data_t *afe_data, bool enabled)
{
    s_playback_gate_on = enabled;
    if (enabled) {
        if (afe_handle->disable_wakenet) {
            afe_handle->disable_wakenet(afe_data);
        }
        if (afe_handle->disable_vad) {
            afe_handle->disable_vad(afe_data);
        }
        ESP_LOGW(TAG, "Playback gate ON: mic pipeline muted, WakeNet/VAD paused");
        return;
    }

    if (afe_handle->enable_vad) {
        afe_handle->enable_vad(afe_data);
    }
    if (afe_handle->enable_wakenet) {
        afe_handle->enable_wakenet(afe_data);
    }
    ESP_LOGI(TAG, "Playback gate OFF: mic pipeline resumed, WakeNet/VAD resumed");
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
    ESP_RETURN_ON_ERROR(i2s_channel_init_pdm_rx_mode(s_mic_rx_chan, &pdm_rx_cfg), TAG, "init mic pdm mode failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_mic_rx_chan), TAG, "enable mic channel failed");
    ESP_LOGI(TAG, "MIC ready: PDM RX, sr=%d, clk=%d, data=%d", SR_SAMPLE_RATE_HZ, MIC_PDM_CLK_GPIO, MIC_PDM_DATA_GPIO);
    return ESP_OK;
}

static esp_err_t init_spk_i2s(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_spk_tx_chan, NULL), TAG, "new spk channel failed");

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SR_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_MSB_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = SPK_BCLK_GPIO,
            .ws = SPK_LRCK_GPIO,
            .dout = SPK_DIN_GPIO,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_spk_tx_chan, &std_cfg), TAG, "init spk std mode failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_spk_tx_chan), TAG, "enable spk channel failed");
    ESP_LOGI(TAG, "SPK ready: I2S TX, sr=%d, bclk=%d, ws=%d, dout=%d", SR_SAMPLE_RATE_HZ, SPK_BCLK_GPIO, SPK_LRCK_GPIO, SPK_DIN_GPIO);
    return ESP_OK;
}

static esp_err_t play_demo_tone(void)
{
    const int16_t amplitude = 7000;
    const uint32_t total_samples = (SR_SAMPLE_RATE_HZ * DEMO_PLAYBACK_DURATION_MS) / 1000;
    const uint32_t period_samples = (SR_SAMPLE_RATE_HZ / DEMO_PLAYBACK_FREQ_HZ) ? (SR_SAMPLE_RATE_HZ / DEMO_PLAYBACK_FREQ_HZ) : 1;
    const size_t frame_samples = 256;
    size_t sample_index = 0;

    int16_t *stereo_frame = heap_caps_malloc(frame_samples * 2 * sizeof(int16_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!stereo_frame) {
        return ESP_ERR_NO_MEM;
    }

    while (sample_index < total_samples) {
        size_t once = frame_samples;
        if ((sample_index + once) > total_samples) {
            once = total_samples - sample_index;
        }

        for (size_t i = 0; i < once; ++i, ++sample_index) {
            int16_t mono = ((sample_index % period_samples) < (period_samples / 2)) ? amplitude : -amplitude;
            stereo_frame[i * 2] = mono;
            stereo_frame[i * 2 + 1] = mono;
        }

        size_t bytes_written = 0;
        esp_err_t ret = i2s_channel_write(s_spk_tx_chan, stereo_frame, once * 2 * sizeof(int16_t), &bytes_written, portMAX_DELAY);
        if (ret != ESP_OK) {
            free(stereo_frame);
            return ret;
        }
    }

    free(stereo_frame);
    return ESP_OK;
}

static void run_playback_stage(esp_afe_sr_iface_t *afe_handle, esp_afe_sr_data_t *afe_data)
{
    set_playback_gate(afe_handle, afe_data, true);
    esp_err_t ret = play_demo_tone();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "demo playback failed: %s", esp_err_to_name(ret));
    } else {
        ESP_LOGI(TAG, "demo playback finished");
    }
    set_playback_gate(afe_handle, afe_data, false);
}

static void reset_segment_state(segment_state_t *state)
{
    state->segment_active = false;
    state->got_speech = false;
    state->tail_silence_ms = 0;
    state->elapsed_ms = 0;
    state->segment_pcm_bytes = 0;
}

static void sr_pipeline_task(void *arg)
{
    sr_runtime_ctx_t *ctx = (sr_runtime_ctx_t *)arg;
    segment_state_t segment = {0};

    ESP_LOGI(TAG, "SR pipeline started, feed_chunksize=%d, feed_nch=%d, chunk_ms=%d",
             ctx->feed_chunksize, ctx->feed_nch, ctx->feed_chunk_ms);

    for (;;) {
        if (s_playback_gate_on) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        size_t bytes_read = 0;
        esp_err_t ret = i2s_channel_read(s_mic_rx_chan, ctx->feed_buffer, ctx->feed_buffer_size_bytes, &bytes_read, pdMS_TO_TICKS(1000));
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
            ESP_LOGI(TAG, "WakeNet detected: start local segment capture");
        }

        if (!segment.segment_active) {
            continue;
        }

        segment.elapsed_ms += ctx->feed_chunk_ms;
        if (res->data && res->data_size > 0) {
            segment.segment_pcm_bytes += (uint32_t)res->data_size;
        }

        if (res->vad_state == VAD_SPEECH) {
            if (!segment.got_speech) {
                ESP_LOGI(TAG, "VAD speech observed in current segment");
            }
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

        ESP_LOGI(TAG,
                 "Endpoint detected (%s), elapsed=%d ms, tail_silence=%d ms, pcm_bytes=%" PRIu32,
                 endpoint_by_silence ? "tail_silence" : "timeout",
                 segment.elapsed_ms,
                 segment.tail_silence_ms,
                 segment.segment_pcm_bytes);

        reset_segment_state(&segment);
        run_playback_stage(ctx->afe_handle, ctx->afe_data);
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "ESP32-S3 SR spike boot");
    size_t psram_size = esp_psram_get_size();
    ESP_LOGI(TAG, "Detected PSRAM size: %u bytes", (unsigned)psram_size);
    if (psram_size == 0) {
        ESP_LOGE(TAG, "No PSRAM detected. wn9_hilexin usually requires PSRAM; please switch PSRAM mode to OCT in menuconfig.");
        return;
    }

    ESP_ERROR_CHECK(init_mic_i2s());
    ESP_ERROR_CHECK(init_spk_i2s());

    srmodel_list_t *models = esp_srmodel_init("model");
    if (!models) {
        ESP_LOGE(TAG, "esp_srmodel_init(\"model\") failed, check model partition/config");
        return;
    }

    afe_config_t *afe_cfg = afe_config_init(AFE_INPUT_FORMAT, models, AFE_TYPE_SR, AFE_MODE_LOW_COST);
    if (!afe_cfg) {
        ESP_LOGE(TAG, "afe_config_init failed");
        return;
    }

    afe_cfg->wakenet_init = true;
    afe_cfg->vad_init = true;
    afe_cfg->aec_init = false;

    char *wn_name = esp_srmodel_filter(models, ESP_WN_PREFIX, NULL);
    if (!wn_name) {
        ESP_LOGE(TAG, "No WakeNet model found; enable one in menuconfig");
        return;
    }
    afe_cfg->wakenet_model_name = wn_name;
    ESP_LOGI(TAG, "WakeNet model selected: %s", wn_name);
    if (!strstr(wn_name, "hilexin")) {
        ESP_LOGW(TAG, "Current model is not Hi,Lexin. Please select wn9_hilexin in menuconfig.");
    }

    esp_afe_sr_iface_t *afe_handle = esp_afe_handle_from_config(afe_cfg);
    if (!afe_handle) {
        ESP_LOGE(TAG, "esp_afe_handle_from_config failed");
        return;
    }

    esp_afe_sr_data_t *afe_data = afe_handle->create_from_config(afe_cfg);
    if (!afe_data) {
        ESP_LOGE(TAG, "afe create_from_config failed");
        return;
    }

    static sr_runtime_ctx_t s_ctx;
    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.afe_handle = afe_handle;
    s_ctx.afe_data = afe_data;
    s_ctx.feed_chunksize = afe_handle->get_feed_chunksize(afe_data);
    s_ctx.feed_nch = afe_handle->get_feed_channel_num(afe_data);
    s_ctx.feed_chunk_ms = (s_ctx.feed_chunksize * 1000) / SR_SAMPLE_RATE_HZ;
    if (s_ctx.feed_chunk_ms <= 0) {
        s_ctx.feed_chunk_ms = 1;
    }
    s_ctx.feed_buffer_size_bytes = s_ctx.feed_chunksize * s_ctx.feed_nch * sizeof(int16_t);
    s_ctx.feed_buffer = heap_caps_calloc(1, s_ctx.feed_buffer_size_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_ctx.feed_buffer) {
        s_ctx.feed_buffer = heap_caps_calloc(1, s_ctx.feed_buffer_size_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    }
    if (!s_ctx.feed_buffer) {
        ESP_LOGE(TAG, "feed buffer alloc failed");
        return;
    }

    BaseType_t ok = xTaskCreatePinnedToCore(sr_pipeline_task, "sr_pipeline_task", 8 * 1024, &s_ctx, 5, NULL, 1);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "failed to create sr pipeline task");
        return;
    }

    ESP_LOGI(TAG, "SR spike is running. Say wake word and watch endpoint + playback mute logs.");
}
