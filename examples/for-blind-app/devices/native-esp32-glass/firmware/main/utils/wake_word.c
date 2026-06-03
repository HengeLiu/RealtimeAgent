#include "wake_word.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "model_path.h"
#include <string.h>

static const char *TAG = "wake_word";

static bool s_wake_word_inited = false;
static wake_word_state_t s_state = WAKE_WORD_STATE_IDLE;
static wake_word_callback_t s_callback = NULL;
static TaskHandle_t s_wake_task = NULL;

// WakeNet handles
static srmodel_list_t *s_models = NULL;
static esp_wn_iface_t *s_wakenet = NULL;
static model_iface_data_t *s_model_data = NULL;

// Audio buffer for WakeNet - needs to hold enough samples for detection
static int16_t *s_audio_buffer = NULL;
static size_t s_audio_chunksize = 0;

// Ring buffer to accumulate audio between detections
static int16_t *s_ring_buffer = NULL;
static size_t s_ring_write_pos = 0;
static size_t s_ring_size = 0;

// Queue to send audio data to wake word task
static QueueHandle_t s_wake_audio_queue = NULL;

#define WAKE_AUDIO_QUEUE_DEPTH 5

typedef struct {
    int16_t samples[512];  // Fixed size chunks
    size_t count;
} wake_audio_chunk_t;

static void wake_word_detection_task(void *pvParameters);

esp_err_t wake_word_init(void) {
    if (s_wake_word_inited) {
        return ESP_OK;
    }

    ESP_LOGI(TAG, "Initializing WakeNet...");

    // Initialize models from "model" partition
    s_models = esp_srmodel_init("model");
    if (!s_models) {
        ESP_LOGE(TAG, "Failed to initialize models");
        return ESP_FAIL;
    }

    // Filter for wakenet model (ESP_WN_PREFIX = "wn")
    char *model_name = esp_srmodel_filter(s_models, ESP_WN_PREFIX, NULL);
    if (!model_name) {
        ESP_LOGE(TAG, "No wakenet model found");
        esp_srmodel_deinit(s_models);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Using model: %s", model_name);

    // Get wakenet interface handle
    s_wakenet = (esp_wn_iface_t *)esp_wn_handle_from_name(model_name);
    if (!s_wakenet) {
        ESP_LOGE(TAG, "Failed to get wakenet handle");
        esp_srmodel_deinit(s_models);
        return ESP_FAIL;
    }

    // Create model instance with detection mode (90 = normal, 95 = aggressive)
    s_model_data = s_wakenet->create(model_name, DET_MODE_90);
    if (!s_model_data) {
        ESP_LOGE(TAG, "Failed to create model instance");
        esp_srmodel_deinit(s_models);
        return ESP_FAIL;
    }

    // Get audio chunk size in samples
    int samp_chunksize = s_wakenet->get_samp_chunksize(s_model_data);
    s_audio_chunksize = samp_chunksize * sizeof(int16_t);
    s_audio_buffer = (int16_t *)malloc(s_audio_chunksize);

    if (!s_audio_buffer) {
        ESP_LOGE(TAG, "Failed to allocate audio buffer");
        s_wakenet->destroy(s_model_data);
        esp_srmodel_deinit(s_models);
        return ESP_ERR_NO_MEM;
    }

    // Ring buffer for continuous audio (3 seconds at 16kHz = 48000 samples)
    s_ring_size = 48000 * sizeof(int16_t);
    s_ring_buffer = (int16_t *)malloc(s_ring_size);
    if (!s_ring_buffer) {
        ESP_LOGE(TAG, "Failed to allocate ring buffer");
        free(s_audio_buffer);
        s_wakenet->destroy(s_model_data);
        esp_srmodel_deinit(s_models);
        return ESP_ERR_NO_MEM;
    }
    s_ring_write_pos = 0;

    // Queue for passing audio to detection task
    s_wake_audio_queue = xQueueCreate(WAKE_AUDIO_QUEUE_DEPTH, sizeof(wake_audio_chunk_t));
    if (!s_wake_audio_queue) {
        ESP_LOGE(TAG, "Failed to create wake audio queue");
        free(s_ring_buffer);
        free(s_audio_buffer);
        s_wakenet->destroy(s_model_data);
        esp_srmodel_deinit(s_models);
        return ESP_ERR_NO_MEM;
    }

    s_wake_word_inited = true;
    s_state = WAKE_WORD_STATE_IDLE;
    ESP_LOGI(TAG, "WakeNet init OK (chunk size: %d samples, ring size: %d bytes)",
             samp_chunksize, s_ring_size);

    return ESP_OK;
}

esp_err_t wake_word_start(void) {
    if (!s_wake_word_inited) {
        return ESP_ERR_INVALID_STATE;
    }

    ESP_LOGI(TAG, "Starting wake word detection...");

    s_ring_write_pos = 0;
    xTaskCreatePinnedToCore(&wake_word_detection_task, "wake_word", 8192, NULL, 5, &s_wake_task, 1);

    return ESP_OK;
}

esp_err_t wake_word_stop(void) {
    if (s_wake_task) {
        vTaskDelete(s_wake_task);
        s_wake_task = NULL;
    }
    s_state = WAKE_WORD_STATE_IDLE;
    ESP_LOGI(TAG, "Wake word stopped");
    return ESP_OK;
}

esp_err_t wake_word_feed_audio(const int16_t *audio_samples, size_t num_samples) {
    (void)audio_samples;
    (void)num_samples;
    // Not used - audio comes from ISR callback
    return ESP_OK;
}

wake_word_state_t wake_word_get_state(void) {
    return s_state;
}

esp_err_t wake_word_set_callback(wake_word_callback_t callback) {
    s_callback = callback;
    return ESP_OK;
}

esp_err_t wake_word_trigger_detected(void) {
    s_state = WAKE_WORD_STATE_DETECTED;
    if (s_callback) {
        ESP_LOGI(TAG, "Wake word DETECTED!");
        s_callback();
    }
    s_state = WAKE_WORD_STATE_IDLE;
    return ESP_OK;
}

// Called from audio ISR to feed audio data to wake word detector
void wake_word_on_i2s_data(const int16_t *audio_samples, size_t num_samples) {
    if (!s_wake_word_inited) {
        ESP_LOGW(TAG, "Wake word not initialized");
        return;
    }
    
    if (s_state != WAKE_WORD_STATE_IDLE) {
        ESP_LOGW(TAG, "Wake word state not IDLE: %d", s_state);
        return;
    }

    wake_audio_chunk_t chunk;
    chunk.count = num_samples > 512 ? 512 : num_samples;
    memcpy(chunk.samples, audio_samples, chunk.count * sizeof(int16_t));

    BaseType_t higher_priority_woken = 0;
    xQueueSendFromISR(s_wake_audio_queue, &chunk, &higher_priority_woken);
}

// Detection task that receives audio and runs WakeNet
static int16_t s_detection_buffer[16000 * 3];
static int16_t s_last_detection_buffer[4800];

static void wake_word_detection_task(void *pvParameters) {
    (void)pvParameters;

    ESP_LOGI(TAG, "Wake word detection task started");

    size_t detection_pos = 0;
    size_t last_detection_pos = 0;

    while (1) {
        // Receive audio chunk
        wake_audio_chunk_t chunk;
        BaseType_t received = xQueueReceive(s_wake_audio_queue, &chunk, pdMS_TO_TICKS(100));

        if (received == pdTRUE) {
            // Add to ring buffer
            size_t samples_to_copy = chunk.count;
            size_t ring_samples = s_ring_size / sizeof(int16_t);

            // Write to ring buffer (circular)
            if (s_ring_write_pos + samples_to_copy <= ring_samples) {
                memcpy(s_ring_buffer + s_ring_write_pos, chunk.samples, samples_to_copy * sizeof(int16_t));
                s_ring_write_pos += samples_to_copy;
            } else {
                // Wrap around
                size_t first_part = ring_samples - s_ring_write_pos;
                memcpy(s_ring_buffer + s_ring_write_pos, chunk.samples, first_part * sizeof(int16_t));
                memcpy(s_ring_buffer, chunk.samples + first_part, (samples_to_copy - first_part) * sizeof(int16_t));
                s_ring_write_pos = samples_to_copy - first_part;
            }

            if (detection_pos + samples_to_copy < 16000 * 3) {
                memcpy(s_detection_buffer + detection_pos, chunk.samples, samples_to_copy * sizeof(int16_t));
                detection_pos += samples_to_copy;
            } else {
                size_t shift = samples_to_copy;
                memmove(s_detection_buffer, s_detection_buffer + shift, (16000 * 3 - shift) * sizeof(int16_t));
                memcpy(s_detection_buffer + 16000 * 3 - samples_to_copy, chunk.samples, samples_to_copy * sizeof(int16_t));
            }

            if (last_detection_pos + samples_to_copy <= 4800) {
                memcpy(s_last_detection_buffer + last_detection_pos, chunk.samples, samples_to_copy * sizeof(int16_t));
                last_detection_pos += samples_to_copy;
            } else {
                size_t shift = samples_to_copy;
                memmove(s_last_detection_buffer, s_last_detection_buffer + shift, (4800 - shift) * sizeof(int16_t));
                memcpy(s_last_detection_buffer + 4800 - samples_to_copy, chunk.samples, samples_to_copy * sizeof(int16_t));
                last_detection_pos = 4800;
            }

            if (detection_pos >= s_audio_chunksize / sizeof(int16_t)) {
                int16_t *feed_ptr = s_detection_buffer + detection_pos - (s_audio_chunksize / sizeof(int16_t));
                wakenet_state_t state = s_wakenet->detect(s_model_data, feed_ptr);

                if (state == WAKENET_DETECTED) {
                    ESP_LOGI(TAG, "WakeNet detected! State=%d", state);

                    // Trigger callback
                    s_state = WAKE_WORD_STATE_DETECTED;
                    if (s_callback) {
                        s_callback();
                    }
                    s_state = WAKE_WORD_STATE_IDLE;

                    // Reset detection buffer after trigger
                    detection_pos = 0;
                    last_detection_pos = 0;
                }
            }
        }
    }
}