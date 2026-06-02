#include "adapters/ra_esp32_mic.h"

#include <stdlib.h>
#include <string.h>

#include "driver/i2s_pdm.h"
#include "esp_err.h"
#include "esp_log.h"

struct ra_esp32_mic {
    esp32s3_mic_board_config_t config;
    i2s_chan_handle_t rx_channel;
    bool started;
    size_t chunks_read;
    size_t read_failures;
};

static const char *TAG = "ra_esp32_mic";

static int mic_init_channel(ra_esp32_mic_t *mic) {
    if (mic->rx_channel != NULL) {
        return 0;
    }

    i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    channel_config.dma_desc_num = 8;
    channel_config.dma_frame_num = 320;

    esp_err_t err = i2s_new_channel(&channel_config, NULL, &mic->rx_channel);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mic.i2s new channel failed err=%s", esp_err_to_name(err));
        mic->rx_channel = NULL;
        return -1;
    }

    i2s_pdm_rx_config_t pdm_config = {
        .clk_cfg = I2S_PDM_RX_CLK_DEFAULT_CONFIG(mic->config.sample_rate),
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .clk = (gpio_num_t)mic->config.pdm_clk,
            .din = (gpio_num_t)mic->config.pdm_data,
            .invert_flags = {
                .clk_inv = false,
            },
        },
    };
    err = i2s_channel_init_pdm_rx_mode(mic->rx_channel, &pdm_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mic.i2s init pdm rx failed err=%s", esp_err_to_name(err));
        i2s_del_channel(mic->rx_channel);
        mic->rx_channel = NULL;
        return -1;
    }
    return 0;
}

static int mic_start(void *ctx) {
    ra_esp32_mic_t *mic = (ra_esp32_mic_t *)ctx;
    if (mic_init_channel(mic) != 0) {
        return -1;
    }
    esp_err_t err = i2s_channel_enable(mic->rx_channel);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "mic.i2s enable failed err=%s", esp_err_to_name(err));
        return -1;
    }
    mic->started = true;
    mic->chunks_read = 0;
    ESP_LOGI(TAG, "mic.start sample_rate=%d chunk_ms=%d pins clk=%d data=%d mode=pdm_rx",
             mic->config.sample_rate, mic->config.chunk_ms, mic->config.pdm_clk, mic->config.pdm_data);
    return 0;
}

static int mic_read(void *ctx, uint8_t *out, size_t capacity, size_t *written) {
    ra_esp32_mic_t *mic = (ra_esp32_mic_t *)ctx;
    size_t bytes = (size_t)(mic->config.sample_rate * mic->config.channels * 2 * mic->config.chunk_ms / 1000);
    if (!mic->started || capacity < bytes) {
        return -1;
    }
    size_t bytes_read = 0;
    esp_err_t err = i2s_channel_read(mic->rx_channel, out, bytes, &bytes_read, (uint32_t)(mic->config.chunk_ms + 80));
    if (err != ESP_OK || bytes_read == 0) {
        mic->read_failures++;
        if (mic->read_failures % 50 == 1) {
            ESP_LOGW(TAG, "mic.i2s read failed err=%s bytes=%u failures=%u",
                     esp_err_to_name(err), (unsigned)bytes_read, (unsigned)mic->read_failures);
        }
        return -1;
    }
    *written = bytes_read;
    mic->chunks_read++;
    mic->read_failures = 0;
    if (mic->chunks_read % 250 == 1) {
        ESP_LOGD(TAG, "mic.chunk read bytes=%u chunks=%u", (unsigned)bytes_read, (unsigned)mic->chunks_read);
    }
    return 0;
}

static int mic_stop(void *ctx) {
    ra_esp32_mic_t *mic = (ra_esp32_mic_t *)ctx;
    if (mic->started && mic->rx_channel != NULL) {
        esp_err_t err = i2s_channel_disable(mic->rx_channel);
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "mic.i2s disable failed err=%s", esp_err_to_name(err));
        }
    }
    mic->started = false;
    ESP_LOGI(TAG, "mic.stop chunks=%u", (unsigned)mic->chunks_read);
    return 0;
}

ra_esp32_mic_t *ra_esp32_mic_create(const esp32s3_mic_board_config_t *config) {
    if (config == NULL) {
        return NULL;
    }
    ra_esp32_mic_t *mic = calloc(1, sizeof(*mic));
    if (mic == NULL) {
        return NULL;
    }
    mic->config = *config;
    return mic;
}

void ra_esp32_mic_destroy(ra_esp32_mic_t *mic) {
    if (mic != NULL && mic->rx_channel != NULL) {
        i2s_del_channel(mic->rx_channel);
    }
    free(mic);
}

ra_mic_source_t ra_esp32_mic_as_source(ra_esp32_mic_t *mic) {
    ra_mic_source_t source = {
        .ctx = mic,
        .format = {
            .codec = "pcm16le",
            .sample_rate = mic->config.sample_rate,
            .channels = mic->config.channels,
            .chunk_ms = mic->config.chunk_ms,
        },
        .start = mic_start,
        .read = mic_read,
        .stop = mic_stop,
    };
    return source;
}
