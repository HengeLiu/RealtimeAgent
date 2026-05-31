#ifndef WAKE_WORD_H
#define WAKE_WORD_H

#include "esp_err.h"
#include <stdint.h>

// Wake word detection state
typedef enum {
    WAKE_WORD_STATE_IDLE = 0,
    WAKE_WORD_STATE_DETECTED,
    WAKE_WORD_STATE_PROCESSING
} wake_word_state_t;

// Callback when wake word is detected
typedef void (*wake_word_callback_t)(void);

esp_err_t wake_word_init(void);
esp_err_t wake_word_start(void);
esp_err_t wake_word_stop(void);
esp_err_t wake_word_feed_audio(const int16_t *audio_samples, size_t num_samples);
wake_word_state_t wake_word_get_state(void);
esp_err_t wake_word_set_callback(wake_word_callback_t callback);
esp_err_t wake_word_trigger_detected(void);

// Feed raw PCM audio to wake word detector from ISR callback
void wake_word_on_i2s_data(const int16_t *audio_samples, size_t num_samples);

#endif // WAKE_WORD_H