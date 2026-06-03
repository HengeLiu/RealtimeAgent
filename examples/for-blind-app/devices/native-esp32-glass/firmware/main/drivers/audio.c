#include "drivers/audio.h"
#include "connectivity/ws_stream.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "utils/wake_word.h"
#include <string.h>

static const char *TAG = "audio";

static bool s_audio_inited = false;
static bool s_streaming = false;
static bool s_wake_word_detecting = false;
static TaskHandle_t s_capture_task = NULL;
static TaskHandle_t s_upload_task = NULL;
static TaskHandle_t s_wake_word_task = NULL;
static QueueHandle_t s_audio_queue = NULL;
static audio_speaker_drain_callback_t s_drain_callback = NULL;

#define AUDIO_QUEUE_DEPTH 10

typedef struct {
    uint16_t n;
    uint8_t data[BYTES_PER_CHUNK];
} AudioChunk;

static i2s_chan_handle_t s_i2s_in_handle = NULL;
static i2s_chan_handle_t s_i2s_out_handle = NULL;

static void audio_capture_task_impl(void *pvParameters);
static void audio_upload_task_impl(void *pvParameters);

esp_err_t audio_init(bool init_mic) {
    ESP_LOGI(TAG, "Starting audio init (mic=%d)...", init_mic);
    esp_err_t ret;

    if (init_mic) {
        // I2S input for PDM microphone - RX channel
        i2s_chan_config_t chan_cfg_in = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);

        i2s_std_config_t i2s_in_cfg = {
            .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
            .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(16, I2S_SLOT_MODE_MONO),
            .gpio_cfg = {
                .mclk = GPIO_NUM_NC,
                .bclk = I2S_MIC_CLOCK_PIN,
                .ws = I2S_MIC_CLOCK_PIN,  // PDM mic uses BCLK as WS
                .din = I2S_MIC_DATA_PIN,
                .dout = GPIO_NUM_NC,
            },
        };

        ESP_LOGI(TAG, "Creating I2S input channel...");
        ret = i2s_new_channel(&chan_cfg_in, NULL, &s_i2s_in_handle);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to create I2S input channel: %d", ret);
            return ret;
        }

        ESP_LOGI(TAG, "Initializing I2S input std mode...");
        ret = i2s_channel_init_std_mode(s_i2s_in_handle, &i2s_in_cfg);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to init I2S input std mode: %d", ret);
            return ret;
        }

        ESP_LOGI(TAG, "Enabling I2S input channel...");
        ret = i2s_channel_enable(s_i2s_in_handle);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to enable I2S input channel: %d", ret);
            return ret;
        }
    }

    // I2S output for speaker - TX channel
    i2s_chan_config_t chan_cfg_out = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);

    i2s_std_config_t i2s_out_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(16, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = GPIO_NUM_NC,
            .bclk = I2S_SPK_BCLK,
            .ws = I2S_SPK_LRCK,
            .din = GPIO_NUM_NC,
            .dout = I2S_SPK_DIN,
        },
    };

    ESP_LOGI(TAG, "Creating I2S output channel...");
    ret = i2s_new_channel(&chan_cfg_out, &s_i2s_out_handle, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create I2S output channel: %d", ret);
        return ret;
    }
    
    ESP_LOGI(TAG, "Initializing I2S output std mode...");
    ret = i2s_channel_init_std_mode(s_i2s_out_handle, &i2s_out_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init I2S output std mode: %d", ret);
        return ret;
    }
    
    ESP_LOGI(TAG, "Enabling I2S output channel...");
    ret = i2s_channel_enable(s_i2s_out_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable I2S output channel: %d", ret);
        return ret;
    }

    ESP_LOGI(TAG, "Creating audio queue...");
    s_audio_queue = xQueueCreate(AUDIO_QUEUE_DEPTH, sizeof(AudioChunk));
    if (!s_audio_queue) {
        ESP_LOGE(TAG, "Failed to create audio queue");
        return ESP_ERR_NO_MEM;
    }

    s_audio_inited = true;
    ESP_LOGI(TAG, "Audio init OK (I2S_NUM_AUTO, RX for mic, TX for speaker)");
    return ESP_OK;
}

esp_err_t audio_capture_frame(uint8_t *data, size_t *len) {
    if (!s_audio_inited || !s_streaming) {
        return ESP_ERR_INVALID_STATE;
    }

    int16_t samples[BYTES_PER_CHUNK / 2];
    size_t samples_read = 0;

    esp_err_t ret = i2s_channel_read(s_i2s_in_handle, samples,
                                    BYTES_PER_CHUNK, &samples_read,
                                    100 / portTICK_PERIOD_MS);
    if (ret == ESP_OK && samples_read > 0) {
        memcpy(data, samples, samples_read * 2);
        *len = samples_read * 2;
        return ESP_OK;
    }

    return ret == ESP_OK ? ESP_ERR_TIMEOUT : ret;
}

esp_err_t audio_start_streaming(void) {
    if (!s_audio_inited) return ESP_ERR_INVALID_STATE;
    
    audio_stop_wake_word_detection();
    
    s_streaming = true;

    xTaskCreatePinnedToCore(&audio_capture_task_impl, "mic_cap", 8192, NULL, 4, &s_capture_task, 0);
    xTaskCreatePinnedToCore(&audio_upload_task_impl, "mic_upl", 8192, NULL, 3, &s_upload_task, 1);

    ESP_LOGI(TAG, "Audio streaming started");
    return ESP_OK;
}

esp_err_t audio_stop_streaming(void) {
    s_streaming = false;
    if (s_capture_task) {
        vTaskDelete(s_capture_task);
        s_capture_task = NULL;
    }
    if (s_upload_task) {
        vTaskDelete(s_upload_task);
        s_upload_task = NULL;
    }
    
    audio_start_wake_word_detection();
    
    ESP_LOGI(TAG, "Audio streaming stopped, wake word detection restarted");
    return ESP_OK;
}

esp_err_t audio_play_wav_data(const uint8_t *data, size_t len) {
    if (!s_audio_inited || !s_i2s_out_handle) {
        return ESP_ERR_INVALID_STATE;
    }

    const int16_t *mono = (const int16_t *)data;
    size_t mono_samples = len / 2;
    size_t chunk_size = 512;

    // Allocate stereo buffer in PSRAM to avoid stack overflow
    int32_t *stereo = heap_caps_malloc(chunk_size * 2 * sizeof(int32_t), MALLOC_CAP_SPIRAM);
    if (!stereo) {
        ESP_LOGE(TAG, "Failed to allocate stereo buffer");
        return ESP_ERR_NO_MEM;
    }

    for (size_t offset = 0; offset < mono_samples; offset += chunk_size) {
        size_t count = mono_samples - offset;
        if (count > chunk_size) count = chunk_size;

        for (size_t i = 0; i < count; i++) {
            int32_t s = (int32_t)mono[offset + i];
            stereo[i * 2 + 0] = s << 16;
            stereo[i * 2 + 1] = s << 16;
        }

        size_t bytes_to_write = count * 2 * sizeof(int32_t);
        size_t bytes_written = 0;
        esp_err_t ret = i2s_channel_write(s_i2s_out_handle, stereo, bytes_to_write,
                                           &bytes_written, 100 / portTICK_PERIOD_MS);
        if (ret != ESP_OK) {
            ESP_LOGW(TAG, "i2s_channel_write failed: %d", ret);
            break;
        }
    }

    free(stereo);

    return ESP_OK;
}

static void audio_capture_task_impl(void *pvParameters) {
    (void)pvParameters;
    AudioChunk chunk;

    for (;;) {
        if (s_streaming) {
            int16_t samples[BYTES_PER_CHUNK / 2];
            size_t samples_read = 0;

            esp_err_t ret = i2s_channel_read(s_i2s_in_handle, samples,
                                             BYTES_PER_CHUNK, &samples_read,
                                             100 / portTICK_PERIOD_MS);
            if (ret == ESP_OK && samples_read > 0) {
                chunk.n = samples_read * 2;
                memcpy(chunk.data, samples, chunk.n);

                if (xQueueSend(s_audio_queue, &chunk, 0) != pdPASS) {
                    AudioChunk dump;
                    if (xQueueReceive(s_audio_queue, &dump, 0) == pdPASS) {
                        xQueueSend(s_audio_queue, &chunk, 0);
                    }
                }
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(5));
        }
    }
}

static void audio_upload_task_impl(void *pvParameters) {
    (void)pvParameters;
    for (;;) {
        if (s_streaming) {
            AudioChunk chunk;
            if (xQueueReceive(s_audio_queue, &chunk, pdMS_TO_TICKS(100)) == pdPASS) {
                ws_stream_send_audio(chunk.data, chunk.n);
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

static void wake_word_capture_task_impl(void *pvParameters) {
    (void)pvParameters;
    
    ESP_LOGI(TAG, "Wake word capture task started");
    
    int log_counter = 0;
    
    for (;;) {
        if (s_wake_word_detecting) {
            int16_t samples[BYTES_PER_CHUNK / 2];
            size_t samples_read = 0;

            esp_err_t ret = i2s_channel_read(s_i2s_in_handle, samples,
                                             BYTES_PER_CHUNK, &samples_read,
                                             100 / portTICK_PERIOD_MS);
            if (ret == ESP_OK && samples_read > 0) {
                wake_word_on_i2s_data(samples, samples_read);
                
                log_counter++;
                if (log_counter % 50 == 0) {
                    ESP_LOGI(TAG, "Wake word audio capture running, samples_read=%d", samples_read);
                }
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

esp_err_t audio_start_wake_word_detection(void) {
    if (!s_audio_inited) {
        ESP_LOGE(TAG, "Audio not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    if (s_wake_word_detecting) {
        ESP_LOGW(TAG, "Wake word detection already running");
        return ESP_OK;
    }

    s_wake_word_detecting = true;

    if (!s_wake_word_task) {
        xTaskCreatePinnedToCore(&wake_word_capture_task_impl, "wake_audio", 
                                4096, NULL, 5, &s_wake_word_task, 1);
        ESP_LOGI(TAG, "Wake word capture task created");
    }

    return ESP_OK;
}

esp_err_t audio_stop_wake_word_detection(void) {
    s_wake_word_detecting = false;
    ESP_LOGI(TAG, "Wake word detection stopped");
    return ESP_OK;
}

esp_err_t audio_speaker_start(void) {
    if (!s_audio_inited || !s_i2s_out_handle) {
        return ESP_ERR_INVALID_STATE;
    }
    ESP_LOGI(TAG, "Speaker started");
    return ESP_OK;
}

esp_err_t audio_speaker_stop(void) {
    ESP_LOGI(TAG, "Speaker stopped");
    return ESP_OK;
}

esp_err_t audio_speaker_set_rate(int rate) {
    ESP_LOGI(TAG, "Speaker rate set to %d", rate);
    return ESP_OK;
}

esp_err_t audio_speaker_feed(const uint8_t *data, size_t len) {
    if (!s_audio_inited || !s_i2s_out_handle) {
        return ESP_ERR_INVALID_STATE;
    }
    return audio_play_wav_data(data, len);
}

esp_err_t audio_speaker_drain_start(void) {
    ESP_LOGI(TAG, "Speaker drain started");
    return ESP_OK;
}

esp_err_t audio_speaker_drain_stop(void) {
    ESP_LOGI(TAG, "Speaker drain stopped");
    return ESP_OK;
}

void audio_speaker_set_drain_callback(audio_speaker_drain_callback_t callback) {
    s_drain_callback = callback;
    ESP_LOGI(TAG, "Drain callback set");
}
