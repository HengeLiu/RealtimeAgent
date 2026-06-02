#include "feedback_tone.h"
#include "audio.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <math.h>
#include <string.h>

static const char *TAG = "feedback_tone";

#define TONE_SAMPLE_RATE 16000
#define TONE_MAX_SAMPLES 16000  // 1 second max per tone segment

static int16_t *s_tone_buf = NULL;

static void ensure_buffer(void) {
    if (!s_tone_buf) {
        s_tone_buf = (int16_t *)heap_caps_malloc(TONE_MAX_SAMPLES * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    }
}

static void play_tone(int freq_hz, int duration_ms, float volume) {
    ensure_buffer();
    if (!s_tone_buf) {
        ESP_LOGE(TAG, "Failed to allocate tone buffer");
        return;
    }

    int num_samples = TONE_SAMPLE_RATE * duration_ms / 1000;
    if (num_samples > TONE_MAX_SAMPLES) num_samples = TONE_MAX_SAMPLES;

    // Generate sine wave
    float step = 2.0f * 3.14159f * freq_hz / TONE_SAMPLE_RATE;
    int16_t amp = (int16_t)(16000.0f * volume);
    for (int i = 0; i < num_samples; i++) {
        s_tone_buf[i] = (int16_t)(sinf(step * i) * amp);
    }

    // Play via audio driver
    esp_err_t ret = audio_play_wav_data((const uint8_t *)s_tone_buf, num_samples * sizeof(int16_t));
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "play_wav_data failed: %d", ret);
    }
}

static void play_silence(int duration_ms) {
    ensure_buffer();
    if (!s_tone_buf) return;

    int num_samples = TONE_SAMPLE_RATE * duration_ms / 1000;
    if (num_samples > TONE_MAX_SAMPLES) num_samples = TONE_MAX_SAMPLES;
    memset(s_tone_buf, 0, num_samples * sizeof(int16_t));
    audio_play_wav_data((const uint8_t *)s_tone_buf, num_samples * sizeof(int16_t));
}

void tone_play_beep(int freq_hz, int duration_ms) {
    play_tone(freq_hz, duration_ms, 0.5f);
}

// 等待配网: 1秒上升音
void tone_play_provisioning_start(void) {
    ESP_LOGI(TAG, "Provisioning start tone");
    play_tone(440, 250, 0.4f);  // A4
    play_silence(50);
    play_tone(554, 250, 0.4f);  // C#5
    play_silence(50);
    play_tone(659, 350, 0.5f);  // E5
}

// 配网成功: 上升音阶
void tone_play_pairing_success(void) {
    ESP_LOGI(TAG, "Pairing success tone");
    play_tone(523, 150, 0.5f);  // C5
    play_silence(50);
    play_tone(659, 150, 0.5f);  // E5
    play_silence(50);
    play_tone(784, 150, 0.5f);  // G5
    play_silence(50);
    play_tone(1047, 300, 0.6f); // C6
}

// 配网失败: 下降音阶
void tone_play_pairing_error(void) {
    ESP_LOGI(TAG, "Pairing error tone");
    play_tone(784, 200, 0.5f);  // G5
    play_silence(50);
    play_tone(523, 200, 0.5f);  // C5
    play_silence(50);
    play_tone(392, 400, 0.4f);  // G4
}

// WiFi 连接成功: 叮咚
void tone_play_wifi_connected(void) {
    ESP_LOGI(TAG, "WiFi connected tone");
    play_tone(880, 100, 0.5f);  // A5
    play_silence(50);
    play_tone(1320, 150, 0.5f); // E6
}

// 正常开机: 短促提示音
void tone_play_startup(void) {
    ESP_LOGI(TAG, "Startup tone");
    play_tone(660, 100, 0.4f);
    play_silence(50);
    play_tone(880, 150, 0.5f);
}

