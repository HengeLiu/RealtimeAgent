#include "drivers/audio.h"
#include "connectivity/ws_stream.h"
#include "driver/i2s_pdm.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/ringbuf.h"
#include "utils/wake_word.h"
#include <string.h>

static const char *TAG = "audio";

static bool s_audio_inited = false;
static bool s_streaming = false;
static bool s_wake_word_detecting = false;
static TaskHandle_t s_capture_task = NULL;
static TaskHandle_t s_upload_task = NULL;
static TaskHandle_t s_wake_word_task = NULL;
static TaskHandle_t s_speaker_task = NULL;
static QueueHandle_t s_audio_queue = NULL;
static RingbufHandle_t s_speaker_ring = NULL;
static bool s_speaker_active = false;
static bool s_speaker_draining = false;
static bool s_speaker_feeding = false;  // true only while server is sending audio (between open.requested and drain complete)
static int s_speaker_sample_rate = 16000;
static void (*s_speaker_drain_complete_cb)(void) = NULL;
static int s_total_fed_bytes = 0;  // Track total bytes fed into ring buffer
static int s_total_fed_count = 0;  // Track total chunks fed
static TickType_t s_last_feed_tick = 0;  // Last time audio was fed into ring buffer

// I2S channel handles
static i2s_chan_handle_t s_pdm_rx_handle = NULL;
static i2s_chan_handle_t s_std_tx_handle = NULL;

#define AUDIO_QUEUE_DEPTH 10
#define SPEAKER_RINGBUF_SIZE (1024 * 1024)  // 1MB PSRAM ring buffer (~21s at 24kHz/16bit/mono)
#define SPEAKER_CHUNK_MAX 4096

typedef struct {
    uint16_t n;
    uint8_t data[BYTES_PER_CHUNK];
} AudioChunk;

static void audio_capture_task_impl(void *pvParameters);
static void audio_upload_task_impl(void *pvParameters);

esp_err_t audio_init(void) {
    // ===== PDM RX for microphone (new channel API) =====
    i2s_chan_config_t chan_cfg_in = {
        .id = I2S_NUM_0,
        .role = I2S_ROLE_MASTER,
        .dma_desc_num = 10,
        .dma_frame_num = 256,
        .auto_clear = false,
    };

    esp_err_t ret = i2s_new_channel(&chan_cfg_in, NULL, &s_pdm_rx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create PDM RX channel: %d", ret);
        return ret;
    }

    i2s_pdm_rx_config_t pdm_rx_cfg = {
        .clk_cfg = {
            .sample_rate_hz = SAMPLE_RATE,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
            .dn_sample_mode = I2S_PDM_DSR_8S,
        },
        .slot_cfg = I2S_PDM_RX_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .clk = I2S_MIC_CLOCK_PIN,
            .din = I2S_MIC_DATA_PIN,
            .invert_flags = { .clk_inv = false },
        },
    };

    ret = i2s_channel_init_pdm_rx_mode(s_pdm_rx_handle, &pdm_rx_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init PDM RX mode: %d", ret);
        return ret;
    }

    ret = i2s_channel_enable(s_pdm_rx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable PDM RX channel: %d", ret);
        return ret;
    }
    ESP_LOGI(TAG, "PDM RX init OK (I2S0, %dHz, DSR_8S)", SAMPLE_RATE);

    // ===== STD TX for speaker =====
    i2s_chan_config_t chan_cfg_out = {
        .id = I2S_NUM_1,
        .role = I2S_ROLE_MASTER,
        .dma_desc_num = 12,
        .dma_frame_num = 256,
        .auto_clear = true,
    };

    ret = i2s_new_channel(&chan_cfg_out, &s_std_tx_handle, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create STD TX channel: %d", ret);
        return ret;
    }

    i2s_std_config_t std_tx_cfg = {
        .clk_cfg = {
            .sample_rate_hz = SAMPLE_RATE,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_32BIT,
            .slot_mode = I2S_SLOT_MODE_STEREO,
            .slot_mask = I2S_STD_SLOT_BOTH,
            .ws_width = I2S_DATA_BIT_WIDTH_32BIT,
            .ws_pol = false,
            .bit_shift = true,
        },
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = I2S_SPK_BCLK,
            .ws = I2S_SPK_LRCK,
            .dout = I2S_SPK_DIN,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };

    ret = i2s_channel_init_std_mode(s_std_tx_handle, &std_tx_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init STD TX mode: %d", ret);
        return ret;
    }

    ret = i2s_channel_enable(s_std_tx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable STD TX channel: %d", ret);
        return ret;
    }
    ESP_LOGI(TAG, "Speaker init OK (I2S1)");

    s_audio_queue = xQueueCreate(AUDIO_QUEUE_DEPTH, sizeof(AudioChunk));
    if (!s_audio_queue) {
        ESP_LOGE(TAG, "Failed to create audio queue");
        return ESP_ERR_NO_MEM;
    }

    // Speaker ring buffer — large enough to absorb burst delivery from server
    // ESP-IDF heap includes PSRAM, so xRingbufferCreate will use PSRAM for large allocations
    s_speaker_ring = xRingbufferCreate(SPEAKER_RINGBUF_SIZE, RINGBUF_TYPE_BYTEBUF);
    if (!s_speaker_ring) {
        ESP_LOGE(TAG, "Failed to create speaker ring buffer (%dKB)", SPEAKER_RINGBUF_SIZE / 1024);
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "Speaker ring buffer created: %dKB, free_heap=%u",
             SPEAKER_RINGBUF_SIZE / 1024, (unsigned)esp_get_free_heap_size());
    if (!s_speaker_ring) {
        ESP_LOGE(TAG, "Failed to create speaker ring buffer");
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "Speaker ring buffer created: %dKB", SPEAKER_RINGBUF_SIZE / 1024);

    s_audio_inited = true;
    ESP_LOGI(TAG, "Audio init OK (new I2S channel API)");
    return ESP_OK;
}

esp_err_t audio_capture_frame(uint8_t *data, size_t *len) {
    if (!s_audio_inited || !s_streaming) {
        return ESP_ERR_INVALID_STATE;
    }

    size_t bytes_read = 0;
    esp_err_t ret = i2s_channel_read(s_pdm_rx_handle, data, BYTES_PER_CHUNK, &bytes_read, 100);
    if (ret == ESP_OK && bytes_read > 0) {
        *len = bytes_read;
        return ESP_OK;
    }

    return ret == ESP_OK ? ESP_ERR_TIMEOUT : ret;
}

esp_err_t audio_start_streaming(void) {
    if (!s_audio_inited) return ESP_ERR_INVALID_STATE;

    audio_stop_wake_word_detection();

    s_streaming = true;

    ESP_LOGI(TAG, "Creating audio tasks, heap free=%u", (unsigned)esp_get_free_heap_size());

    BaseType_t ret1 = xTaskCreatePinnedToCore(&audio_capture_task_impl, "mic_cap", 6144, NULL, 4, &s_capture_task, 0);
    BaseType_t ret2 = xTaskCreatePinnedToCore(&audio_upload_task_impl, "mic_upl", 6144, NULL, 3, &s_upload_task, 1);

    if (ret1 != pdPASS || ret2 != pdPASS) {
        ESP_LOGE(TAG, "Failed to create audio tasks: cap=%d upl=%d", ret1, ret2);
        s_streaming = false;
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Audio streaming started, heap free=%u", (unsigned)esp_get_free_heap_size());
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

    return ESP_OK;
}

esp_err_t audio_play_wav_data(const uint8_t *data, size_t len) {
    if (!s_audio_inited) {
        return ESP_ERR_INVALID_STATE;
    }

    const int16_t *mono = (const int16_t *)data;
    size_t mono_samples = len / 2;

    // Diagnostic: log first few chunks
    static int s_play_count = 0;
    s_play_count++;
    if (s_play_count <= 3) {
        int peak = 0;
        for (size_t i = 0; i < mono_samples && i < 100; i++) {
            int v = mono[i] > 0 ? mono[i] : -mono[i];
            if (v > peak) peak = v;
        }
        ESP_LOGI(TAG, "play_wav #%d: len=%d samples=%d rate=%d peak=%d",
                 s_play_count, (int)len, (int)mono_samples, s_speaker_sample_rate, peak);
    }

    // Convert mono16 -> stereo32 (MSB-aligned, like MAX98357A expects)
    // Process in chunks to avoid large buffer allocation
    static int32_t *stereo32 = NULL;
    if (!stereo32) {
        stereo32 = (int32_t *)heap_caps_malloc(1024 * 2 * sizeof(int32_t), MALLOC_CAP_SPIRAM);
        if (!stereo32) {
            ESP_LOGE(TAG, "Failed to allocate stereo32 buffer in PSRAM");
            return ESP_ERR_NO_MEM;
        }
    }

    #define PLAY_CHUNK_SAMPLES 1024
    size_t processed = 0;
    while (processed < mono_samples) {
        size_t chunk = mono_samples - processed;
        if (chunk > PLAY_CHUNK_SAMPLES) chunk = PLAY_CHUNK_SAMPLES;

        // Convert this chunk
        for (size_t i = 0; i < chunk; i++) {
            int32_t s = (int32_t)((float)mono[processed + i] * 0.8f);
            int32_t v32 = s << 16;  // MSB-align for 32-bit I2S
            stereo32[i * 2 + 0] = v32;  // L
            stereo32[i * 2 + 1] = v32;  // R
        }

        // Write this chunk to I2S
        size_t chunk_bytes = chunk * 2 * sizeof(int32_t);
        size_t offset = 0;
        while (offset < chunk_bytes) {
            size_t bytes_written = 0;
            esp_err_t ret = i2s_channel_write(s_std_tx_handle, (uint8_t *)stereo32 + offset,
                                               chunk_bytes - offset, &bytes_written, 200);
            if (ret != ESP_OK || bytes_written == 0) break;
            offset += bytes_written;
        }

        processed += chunk;
    }

    return ESP_OK;
}

// Pre-buffer: minimal buffering before starting playback
// Server sends audio in bursts, so we need very little pre-buffering
#define PREBUFFER_COUNT 3
// Use blocking receive with timeout instead of polling
#define RECV_TIMEOUT_MS 50
// Drain completes only after this long with no new data arriving
// Must be long enough to span gaps between server burst batches (~30-40ms apart)
#define DRAIN_IDLE_MS 500

static void speaker_playback_task(void *pvParameters) {
    (void)pvParameters;
    ESP_LOGI(TAG, "Speaker playback task started (prebuf=%d, drain_idle=%dms)", PREBUFFER_COUNT, DRAIN_IDLE_MS);

    int play_count = 0;
    int total_bytes = 0;
    int underrun_count = 0;
    bool prebuffering = true;

    while (s_speaker_active) {
        // === Pre-buffer phase: wait for enough data before starting playback ===
        if (prebuffering) {
            UBaseType_t uxItems;
            vRingbufferGetInfo(s_speaker_ring, NULL, NULL, NULL, NULL, &uxItems);
            if (uxItems >= PREBUFFER_COUNT || (s_speaker_draining && uxItems > 0)) {
                ESP_LOGI(TAG, "Pre-buffer ready: %d chunks", (int)uxItems);
                prebuffering = false;
                play_count = 0;
                total_bytes = 0;
                underrun_count = 0;
            } else {
                vTaskDelay(pdMS_TO_TICKS(20));
                continue;
            }
        }

        // === Playback phase: blocking receive with timeout ===
        size_t item_size = 0;
        void *item = xRingbufferReceive(s_speaker_ring, &item_size, pdMS_TO_TICKS(RECV_TIMEOUT_MS));

        if (item != NULL) {
            play_count++;
            total_bytes += item_size;
            if (play_count <= 5 || play_count % 100 == 0) {
                ESP_LOGI(TAG, "Playing #%d: %d bytes (%dKB total, %d underruns)",
                         play_count, item_size, total_bytes / 1024, underrun_count);
            }
            underrun_count = 0;  // Reset underrun counter on successful receive

            // Play the full chunk directly - audio_play_wav_data handles piece splitting
            audio_play_wav_data((const uint8_t *)item, item_size);
            vRingbufferReturnItem(s_speaker_ring, item);

            // NOTE: Do NOT check drain completion here!
            // The ring buffer merges small items, so it can appear momentarily empty
            // between batches from the server. Drain is only checked in the timeout
            // path below, when we've been idle long enough to confirm no more data.
        } else {
            // Timeout — no data available for RECV_TIMEOUT_MS
            underrun_count++;
            if (underrun_count <= 3 || underrun_count % 50 == 0) {
                ESP_LOGW(TAG, "Speaker underrun #%d (buf empty for %dms)", underrun_count, RECV_TIMEOUT_MS);
            }

            // Drain check: only complete when idle long enough after last feed
            // This prevents premature drain when server sends audio in multiple bursts
            if (s_speaker_draining) {
                TickType_t idle_ticks = xTaskGetTickCount() - s_last_feed_tick;
                int idle_ms = (int)(idle_ticks * portTICK_PERIOD_MS);
                if (idle_ms >= DRAIN_IDLE_MS) {
                    // Double-check ring buffer is truly empty
                    UBaseType_t uxItems;
                    vRingbufferGetInfo(s_speaker_ring, NULL, NULL, NULL, NULL, &uxItems);
                    if (uxItems == 0) {
                        ESP_LOGI(TAG, "Drain complete: played=%d/%d chunks, %d/%dKB, %d underruns, idle=%dms",
                                 play_count, s_total_fed_count, total_bytes / 1024, s_total_fed_bytes / 1024, underrun_count, idle_ms);
                        s_speaker_draining = false;
                        s_speaker_feeding = false;
                        if (s_speaker_drain_complete_cb) {
                            s_speaker_drain_complete_cb();
                        }
                        prebuffering = true;
                    } else {
                        ESP_LOGI(TAG, "Drain idle=%dms but %d items still in buffer, continuing", idle_ms, (int)uxItems);
                    }
                }
            }
        }
    }

    s_speaker_active = false;
    s_speaker_draining = false;
    s_speaker_feeding = false;
    s_speaker_task = NULL;
    ESP_LOGI(TAG, "Speaker playback task exiting");
    vTaskDelete(NULL);
}

esp_err_t audio_speaker_start(void) {
    if (!s_audio_inited) return ESP_ERR_INVALID_STATE;

    // If already active and draining, let the current playback finish naturally
    // Don't clear the ring buffer — leftover audio will play through
    if (s_speaker_active) {
        s_speaker_draining = false;  // Cancel drain, accept new audio
        s_speaker_feeding = true;    // Re-enable feeding (mute mic)
        ESP_LOGI(TAG, "Speaker already active, resuming feed");
        return ESP_OK;
    }

    s_speaker_feeding = true;  // Mute mic while server is sending audio

    s_total_fed_bytes = 0;
    s_total_fed_count = 0;
    s_last_feed_tick = xTaskGetTickCount();
    s_speaker_active = true;
    BaseType_t ret = xTaskCreatePinnedToCore(&speaker_playback_task, "spk_play", 10240, NULL, 3, &s_speaker_task, 1);
    if (ret != pdPASS) {
        ESP_LOGE(TAG, "Failed to create speaker task");
        s_speaker_active = false;
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Speaker output started");
    return ESP_OK;
}

esp_err_t audio_speaker_stop(void) {
    s_speaker_active = false;
    s_speaker_draining = false;
    s_speaker_feeding = false;
    // Flush ring buffer
    size_t item_size;
    void *item;
    while ((item = xRingbufferReceive(s_speaker_ring, &item_size, 0)) != NULL) {
        vRingbufferReturnItem(s_speaker_ring, item);
    }
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_LOGI(TAG, "Speaker output stopped");
    return ESP_OK;
}

esp_err_t audio_speaker_drain_stop(void) {
    if (!s_speaker_active) return ESP_OK;

    // Signal drain — playback task will continue consuming ring buffer
    // and auto-stop when empty after 5 seconds idle
    s_speaker_draining = true;
    ESP_LOGI(TAG, "Speaker draining signaled, playback continues");
    return ESP_OK;
}

esp_err_t audio_speaker_feed(const uint8_t *pcm_data, size_t len) {
    if (!s_audio_inited || !s_speaker_active || !s_speaker_ring) {
        static int s_feed_drop = 0;
        s_feed_drop++;
        if (s_feed_drop <= 3 || s_feed_drop % 100 == 0) {
            ESP_LOGW(TAG, "audio_speaker_feed DROP #%d: inited=%d active=%d ring=%p",
                     s_feed_drop, s_audio_inited, s_speaker_active, (void *)s_speaker_ring);
        }
        return ESP_ERR_INVALID_STATE;
    }
    if (len == 0 || len > SPEAKER_CHUNK_MAX) {
        ESP_LOGW(TAG, "audio_speaker_feed INVALID_ARG: len=%d max=%d", (int)len, SPEAKER_CHUNK_MAX);
        return ESP_ERR_INVALID_ARG;
    }

    // Ring buffer handles overflow naturally — when full, xRingbufferSend returns pdFALSE
    // and we silently drop the chunk (better than blocking the WS handler)
    if (xRingbufferSend(s_speaker_ring, pcm_data, len, 0) != pdTRUE) {
        static int s_drop = 0;
        s_drop++;
        if (s_drop <= 3 || s_drop % 100 == 0) {
            ESP_LOGW(TAG, "Speaker ring buffer full, dropping chunk #%d (%d bytes)", s_drop, len);
        }
    } else {
        s_total_fed_bytes += len;
        s_total_fed_count++;
        s_last_feed_tick = xTaskGetTickCount();
        if (s_total_fed_count <= 3 || s_total_fed_count % 50 == 0) {
            ESP_LOGI(TAG, "Speaker fed #%d: %d bytes (%dKB total, draining=%d)",
                     s_total_fed_count, (int)len, s_total_fed_bytes / 1024, s_speaker_draining);
        }
    }
    return ESP_OK;
}

void audio_speaker_set_drain_callback(void (*cb)(void)) {
    s_speaker_drain_complete_cb = cb;
}

esp_err_t audio_speaker_set_rate(int sample_rate) {
    if (!s_audio_inited || !s_std_tx_handle) {
        ESP_LOGE(TAG, "audio_speaker_set_rate FAIL: inited=%d handle=%p", s_audio_inited, (void*)s_std_tx_handle);
        return ESP_ERR_INVALID_STATE;
    }
    if (sample_rate == s_speaker_sample_rate) return ESP_OK;

    ESP_LOGI(TAG, "Speaker rate: %d -> %d", s_speaker_sample_rate, sample_rate);
    s_speaker_sample_rate = sample_rate;

    // Must disable channel before reconfiguring clock
    i2s_channel_disable(s_std_tx_handle);
    i2s_std_clk_config_t clk_cfg = {
        .sample_rate_hz = sample_rate,
        .clk_src = I2S_CLK_SRC_DEFAULT,
        .mclk_multiple = I2S_MCLK_MULTIPLE_256,
    };
    esp_err_t ret = i2s_channel_reconfig_std_clock(s_std_tx_handle, &clk_cfg);
    i2s_channel_enable(s_std_tx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to reconfig speaker clock: %d", ret);
    }
    return ret;
}

static void audio_capture_task_impl(void *pvParameters) {
    (void)pvParameters;
    AudioChunk chunk;

    for (;;) {
        if (s_streaming) {
            uint8_t buf[BYTES_PER_CHUNK];
            size_t bytes_read = 0;

            esp_err_t ret = i2s_channel_read(s_pdm_rx_handle, buf, BYTES_PER_CHUNK, &bytes_read, 100);
            if (ret == ESP_OK && bytes_read > 0) {
                wake_word_on_i2s_data((const int16_t *)buf, bytes_read / 2);

                chunk.n = bytes_read;
                memcpy(chunk.data, buf, chunk.n);

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
    int upload_count = 0;
    for (;;) {
        if (s_streaming) {
            AudioChunk chunk;
            if (xQueueReceive(s_audio_queue, &chunk, pdMS_TO_TICKS(100)) == pdPASS) {
                // Mute mic upload while server is sending speaker audio to prevent feedback loop
                if (s_speaker_feeding) {
                    // Drop audio — speaker is receiving, don't send to server
                    continue;
                }
                esp_err_t ret = ws_stream_send_audio(chunk.data, chunk.n);
                upload_count++;
                if (upload_count <= 5 || upload_count % 100 == 0) {
                    ESP_LOGI(TAG, "Audio upload #%d: n=%d ret=%d", upload_count, chunk.n, ret);
                }
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

static void wake_word_capture_task_impl(void *pvParameters) {
    (void)pvParameters;

    ESP_LOGI(TAG, "Wake word capture task STARTED, detecting=%d, pmd_rx=%p",
             s_wake_word_detecting, (void *)s_pdm_rx_handle);
    int read_count = 0;
    int timeout_count = 0;
    int error_count = 0;

    while (s_wake_word_detecting) {
        uint8_t buf[BYTES_PER_CHUNK];
        size_t bytes_read = 0;

        esp_err_t ret = i2s_channel_read(s_pdm_rx_handle, buf, BYTES_PER_CHUNK, &bytes_read, 100);
        if (!s_wake_word_detecting) break;  // Check again after blocking read

        read_count++;
        if (read_count <= 5 || read_count % 200 == 0) {
            ESP_LOGI(TAG, "I2S read #%d: ret=%d bytes=%d err=%d timeout=%d",
                     read_count, ret, (int)bytes_read, error_count, timeout_count);
        }
        if (ret == ESP_OK && bytes_read > 0) {
            timeout_count = 0;
            error_count = 0;
            wake_word_on_i2s_data((const int16_t *)buf, bytes_read / 2);
        } else if (ret == ESP_ERR_TIMEOUT) {
            timeout_count++;
            if (timeout_count > 50) {
                ESP_LOGW(TAG, "Too many timeouts, restarting I2S...");
                i2s_channel_disable(s_pdm_rx_handle);
                vTaskDelay(pdMS_TO_TICKS(10));
                i2s_channel_enable(s_pdm_rx_handle);
                timeout_count = 0;
            }
        } else {
            error_count++;
            if (error_count <= 5 || error_count % 50 == 0) {
                ESP_LOGE(TAG, "I2S read error #%d: ret=%d", error_count, ret);
            }
            // Channel disabled (e.g. stopped externally) — exit cleanly
            if (ret == ESP_ERR_INVALID_STATE) break;
        }
    }

    ESP_LOGI(TAG, "Wake word capture task EXITING");
    s_wake_word_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t audio_start_wake_word_detection(void) {
    if (!s_audio_inited) {
        ESP_LOGE(TAG, "Audio not initialized - cannot start wake word");
        return ESP_ERR_INVALID_STATE;
    }

    if (s_wake_word_detecting) {
        ESP_LOGW(TAG, "Wake word detection already running");
        return ESP_OK;
    }

    s_wake_word_detecting = true;

    ESP_LOGI(TAG, "Creating wake word capture task, heap free=%u, pdm_rx=%p",
             (unsigned)esp_get_free_heap_size(), (void *)s_pdm_rx_handle);

    BaseType_t ret = xTaskCreatePinnedToCore(&wake_word_capture_task_impl, "wake_cap", 5120, NULL, 4, &s_wake_word_task, 0);
    if (ret != pdPASS) {
        ESP_LOGE(TAG, "Failed to create wake word task: %d", ret);
        s_wake_word_detecting = false;
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Wake word detection started, heap free=%u", (unsigned)esp_get_free_heap_size());
    return ESP_OK;
}

esp_err_t audio_stop_wake_word_detection(void) {
    if (!s_wake_word_detecting) return ESP_OK;

    s_wake_word_detecting = false;

    // Disable I2S to force any blocking read to return, allowing task to self-exit
    i2s_channel_disable(s_pdm_rx_handle);

    // Wait for the task to self-terminate (it checks s_wake_word_detecting and exits)
    for (int i = 0; i < 50 && s_wake_word_task != NULL; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    // Re-enable I2S for the next user (audio streaming)
    i2s_channel_enable(s_pdm_rx_handle);

    wake_word_stop();

    ESP_LOGI(TAG, "Wake word detection stopped, I2S re-enabled");
    return ESP_OK;
}
