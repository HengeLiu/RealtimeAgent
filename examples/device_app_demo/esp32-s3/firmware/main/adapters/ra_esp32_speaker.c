#include "adapters/ra_esp32_speaker.h"

#include <stdlib.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

struct ra_esp32_speaker {
    esp32s3_speaker_board_config_t config;
    i2s_chan_handle_t tx_channel;
    size_t bytes_written;
    size_t expanded_bytes_written;
    size_t chunks_written;
    int prepared_sample_rate;
    bool enabled;
};

static const char *TAG = "ra_esp32_speaker";

static int speaker_delete_channel(ra_esp32_speaker_t *speaker) {
    if (speaker->tx_channel == NULL) {
        return 0;
    }
    if (speaker->enabled) {
        esp_err_t disable_err = i2s_channel_disable(speaker->tx_channel);
        if (disable_err != ESP_OK && disable_err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "speaker.i2s disable before delete failed err=%s", esp_err_to_name(disable_err));
        }
        speaker->enabled = false;
    }
    esp_err_t err = i2s_del_channel(speaker->tx_channel);
    speaker->tx_channel = NULL;
    speaker->prepared_sample_rate = 0;
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "speaker.i2s delete failed err=%s", esp_err_to_name(err));
        return -1;
    }
    return 0;
}

static int speaker_init_channel(ra_esp32_speaker_t *speaker, int sample_rate) {
    if (speaker->tx_channel != NULL && speaker->prepared_sample_rate == sample_rate) {
        return 0;
    }
    if (speaker->tx_channel != NULL) {
        speaker_delete_channel(speaker);
    }

    i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    channel_config.dma_desc_num = 8;
    channel_config.dma_frame_num = 320;
    channel_config.auto_clear = true;

    esp_err_t err = i2s_new_channel(&channel_config, &speaker->tx_channel, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "speaker.i2s new channel failed err=%s", esp_err_to_name(err));
        speaker->tx_channel = NULL;
        return -1;
    }

    i2s_data_bit_width_t bit_width = speaker->config.stereo_32bit_output
        ? I2S_DATA_BIT_WIDTH_32BIT
        : I2S_DATA_BIT_WIDTH_16BIT;
    i2s_slot_mode_t slot_mode = speaker->config.stereo_32bit_output
        ? I2S_SLOT_MODE_STEREO
        : I2S_SLOT_MODE_MONO;
    i2s_std_config_t std_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(sample_rate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(bit_width, slot_mode),
        .gpio_cfg = {
            .mclk = GPIO_NUM_NC,
            .bclk = (gpio_num_t)speaker->config.bclk,
            .ws = (gpio_num_t)speaker->config.lrck,
            .dout = (gpio_num_t)speaker->config.dout,
            .din = GPIO_NUM_NC,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    err = i2s_channel_init_std_mode(speaker->tx_channel, &std_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "speaker.i2s init std tx failed err=%s", esp_err_to_name(err));
        i2s_del_channel(speaker->tx_channel);
        speaker->tx_channel = NULL;
        return -1;
    }
    speaker->prepared_sample_rate = sample_rate;
    return 0;
}

static int speaker_enable(ra_esp32_speaker_t *speaker) {
    if (speaker->enabled) {
        return 0;
    }
    esp_err_t err = i2s_channel_enable(speaker->tx_channel);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "speaker.i2s enable failed err=%s", esp_err_to_name(err));
        return -1;
    }
    speaker->enabled = true;
    return 0;
}

static int speaker_write_raw(ra_esp32_speaker_t *speaker, const uint8_t *pcm, size_t size, uint32_t timeout_ms) {
    size_t bytes_written = 0;
    esp_err_t err = i2s_channel_write(speaker->tx_channel, pcm, size, &bytes_written, timeout_ms);
    if (err != ESP_OK || bytes_written != size) {
        ESP_LOGW(TAG, "speaker.i2s write failed err=%s requested=%u written=%u timeout_ms=%u",
                 esp_err_to_name(err), (unsigned)size, (unsigned)bytes_written, (unsigned)timeout_ms);
        return -1;
    }
    return 0;
}

static int speaker_write_stereo_32bit(
    ra_esp32_speaker_t *speaker,
    const uint8_t *pcm,
    size_t size,
    uint32_t timeout_ms,
    size_t *expanded_size
) {
    size_t sample_count = size / sizeof(int16_t);
    size_t out_size = sample_count * 2 * sizeof(int32_t);
    int32_t *out = (int32_t *)malloc(out_size);
    if (out == NULL) {
        ESP_LOGE(TAG, "speaker.expand alloc failed bytes=%u", (unsigned)out_size);
        return -1;
    }

    const int16_t *in = (const int16_t *)pcm;
    for (size_t i = 0; i < sample_count; i++) {
        int32_t sample = ((int32_t)in[i]) << 16;
        out[i * 2] = sample;
        out[i * 2 + 1] = sample;
    }
    int rc = speaker_write_raw(speaker, (const uint8_t *)out, out_size, timeout_ms);
    free(out);
    if (rc == 0 && expanded_size != NULL) {
        *expanded_size = out_size;
    }
    return rc;
}

static int speaker_prepare(void *ctx, const ra_audio_format_t *format) {
    ra_esp32_speaker_t *speaker = (ra_esp32_speaker_t *)ctx;
    speaker->bytes_written = 0;
    speaker->expanded_bytes_written = 0;
    speaker->chunks_written = 0;
    int sample_rate = format != NULL && format->sample_rate > 0 ? format->sample_rate : speaker->config.sample_rate;
    if (speaker_init_channel(speaker, sample_rate) != 0 || speaker_enable(speaker) != 0) {
        return -1;
    }
    ESP_LOGI(TAG, "speaker.prepare codec=%s sample_rate=%d pins bclk=%d lrck=%d dout=%d mode=%s",
             format == NULL ? "unknown" : format->codec,
             sample_rate,
             speaker->config.bclk,
             speaker->config.lrck,
             speaker->config.dout,
             speaker->config.stereo_32bit_output ? "std_tx_stereo_32bit" : "std_tx_mono_16bit");
    return 0;
}

static int speaker_write(void *ctx, const uint8_t *pcm, size_t size, int duration_ms) {
    ra_esp32_speaker_t *speaker = (ra_esp32_speaker_t *)ctx;
    if (speaker->tx_channel == NULL || !speaker->enabled || pcm == NULL || size == 0) {
        return -1;
    }
    uint32_t timeout_ms = duration_ms > 0 ? (uint32_t)duration_ms + 500 : 1000;
    size_t expanded_size = size;
    int rc = speaker->config.stereo_32bit_output
        ? speaker_write_stereo_32bit(speaker, pcm, size, timeout_ms, &expanded_size)
        : speaker_write_raw(speaker, pcm, size, timeout_ms);
    if (rc != 0) {
        return rc;
    }
    speaker->bytes_written += size;
    speaker->expanded_bytes_written += expanded_size;
    speaker->chunks_written++;
    if (speaker->chunks_written == 1 || speaker->chunks_written % 100 == 0) {
        ESP_LOGD(TAG, "speaker.write pcm_bytes=%u expanded_bytes=%u duration_ms=%d chunks=%u total_pcm=%u",
                 (unsigned)size,
                 (unsigned)expanded_size,
                 duration_ms,
                 (unsigned)speaker->chunks_written,
                 (unsigned)speaker->bytes_written);
    }
    return 0;
}

static int speaker_drain(void *ctx) {
    ra_esp32_speaker_t *speaker = (ra_esp32_speaker_t *)ctx;
    ESP_LOGI(TAG, "speaker.drain chunks=%u bytes_written=%u expanded_bytes=%u",
             (unsigned)speaker->chunks_written,
             (unsigned)speaker->bytes_written,
             (unsigned)speaker->expanded_bytes_written);
    if (speaker->enabled && speaker->tx_channel != NULL) {
        vTaskDelay(pdMS_TO_TICKS(120));
        esp_err_t err = i2s_channel_disable(speaker->tx_channel);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "speaker.i2s drain disable failed err=%s", esp_err_to_name(err));
            return -1;
        }
        speaker->enabled = false;
    }
    return 0;
}

static int speaker_cancel(void *ctx) {
    ra_esp32_speaker_t *speaker = (ra_esp32_speaker_t *)ctx;
    ESP_LOGI(TAG, "speaker.cancel chunks=%u bytes_written=%u expanded_bytes=%u",
             (unsigned)speaker->chunks_written,
             (unsigned)speaker->bytes_written,
             (unsigned)speaker->expanded_bytes_written);
    if (speaker->enabled && speaker->tx_channel != NULL) {
        esp_err_t err = i2s_channel_disable(speaker->tx_channel);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "speaker.i2s cancel disable failed err=%s", esp_err_to_name(err));
        }
        speaker->enabled = false;
    }
    speaker->bytes_written = 0;
    speaker->expanded_bytes_written = 0;
    speaker->chunks_written = 0;
    return 0;
}

ra_esp32_speaker_t *ra_esp32_speaker_create(const esp32s3_speaker_board_config_t *config) {
    if (config == NULL) {
        return NULL;
    }
    ra_esp32_speaker_t *speaker = calloc(1, sizeof(*speaker));
    if (speaker == NULL) {
        return NULL;
    }
    speaker->config = *config;
    return speaker;
}

void ra_esp32_speaker_destroy(ra_esp32_speaker_t *speaker) {
    if (speaker != NULL) {
        speaker_delete_channel(speaker);
    }
    free(speaker);
}

ra_speaker_sink_t ra_esp32_speaker_as_sink(ra_esp32_speaker_t *speaker) {
    ra_speaker_sink_t sink = {
        .ctx = speaker,
        .prepare = speaker_prepare,
        .write = speaker_write,
        .drain = speaker_drain,
        .cancel = speaker_cancel,
    };
    return sink;
}
