#ifndef AUDIO_H
#define AUDIO_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#define I2S_MIC_CLOCK_PIN 42
#define I2S_MIC_DATA_PIN  41
#define I2S_SPK_BCLK     7
#define I2S_SPK_LRCK     8
#define I2S_SPK_DIN      9

#define SAMPLE_RATE     16000
#define CHUNK_MS        20
#define BYTES_PER_CHUNK (SAMPLE_RATE * CHUNK_MS / 1000 * 2)

esp_err_t audio_init(void);
esp_err_t audio_capture_frame(uint8_t *data, size_t *len);
esp_err_t audio_start_streaming(void);
esp_err_t audio_stop_streaming(void);
esp_err_t audio_play_wav_data(const uint8_t *data, size_t len);

// Speaker output pipeline (for server audio playback)
esp_err_t audio_speaker_start(void);
esp_err_t audio_speaker_stop(void);
esp_err_t audio_speaker_drain_stop(void);
void audio_speaker_set_drain_callback(void (*cb)(void));
esp_err_t audio_speaker_feed(const uint8_t *pcm_data, size_t len);
esp_err_t audio_speaker_set_rate(int sample_rate);

esp_err_t audio_start_wake_word_detection(void);
esp_err_t audio_stop_wake_word_detection(void);

#endif // AUDIO_H