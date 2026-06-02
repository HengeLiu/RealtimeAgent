#include "drivers/camera.h"
#include "connectivity/ws_stream.h"
#include "esp_camera.h"
#include "sensor.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "camera";

static bool s_camera_inited = false;
static bool s_streaming = false;
static TaskHandle_t s_capture_task = NULL;
static TaskHandle_t s_send_task = NULL;
static camera_fb_t *s_last_fb = NULL;

esp_err_t camera_init(void) {
    camera_config_t config = {
        .pin_pwdn       = -1,
        .pin_reset      = -1,
        .pin_xclk       = 10,
        .pin_sccb_sda   = 40,
        .pin_sccb_scl   = 39,
        .pin_d7         = 48,
        .pin_d6         = 11,
        .pin_d5         = 12,
        .pin_d4         = 14,
        .pin_d3         = 16,
        .pin_d2         = 18,
        .pin_d1         = 17,
        .pin_d0         = 15,
        .pin_vsync      = 38,
        .pin_href       = 47,
        .pin_pclk       = 13,
        .xclk_freq_hz   = 20000000,
        .ledc_timer     = LEDC_TIMER_0,
        .ledc_channel   = LEDC_CHANNEL_0,
        .pixel_format   = PIXFORMAT_JPEG,
        .frame_size     = FRAMESIZE_VGA,
        .jpeg_quality   = 10,
        .fb_count       = 2,
        .fb_location    = CAMERA_FB_IN_PSRAM,
        .grab_mode      = CAMERA_GRAB_LATEST
    };

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Camera init failed: 0x%x", err);
        return err;
    }

    s_camera_inited = true;
    ESP_LOGI(TAG, "Camera init OK");
    return ESP_OK;
}

esp_err_t camera_capture_frame(uint8_t **jpg_data, size_t *jpg_len) {
    if (!s_camera_inited) {
        return ESP_ERR_INVALID_STATE;
    }

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        ESP_LOGE(TAG, "Failed to get frame buffer");
        return ESP_FAIL;
    }

    *jpg_data = fb->buf;
    *jpg_len = fb->len;
    return ESP_OK;
}

esp_err_t camera_return_frame(void) {
    // Get fb from esp_camera_fb_get() must be returned
    // But we handle it in ws_stream so this is placeholder
    return ESP_OK;
}

esp_err_t camera_set_quality(int quality) {
    (void)quality;
    return ESP_OK;
}

esp_err_t camera_set_framesize(framesize_t framesize) {
    (void)framesize;
    return ESP_OK;
}

esp_err_t camera_capture_hq(uint8_t **jpg_data, size_t *jpg_len) {
    return camera_capture_frame(jpg_data, jpg_len);
}

static void camera_send_task(void *pvParameters) {
    (void)pvParameters;
    for (;;) {
        uint8_t *jpg_data = NULL;
        size_t jpg_len = 0;

        if (s_streaming && camera_capture_frame(&jpg_data, &jpg_len) == ESP_OK) {
            ws_stream_send_image(jpg_data, jpg_len);
            camera_return_frame();
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void camera_task_start(void) {
    if (!s_camera_inited) return;
    s_streaming = true;

    xTaskCreatePinnedToCore(&camera_send_task, "cam_snd", 4096, NULL, 3, &s_send_task, 1);
    ESP_LOGI(TAG, "Camera task started");
}

void camera_task_stop(void) {
    if (!s_camera_inited) return;
    s_streaming = false;
    
    if (s_send_task) {
        vTaskDelete(s_send_task);
        s_send_task = NULL;
    }
    ESP_LOGI(TAG, "Camera task stopped");
}