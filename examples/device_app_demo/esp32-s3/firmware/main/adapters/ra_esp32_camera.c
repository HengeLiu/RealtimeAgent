#include "adapters/ra_esp32_camera.h"

#include <stdlib.h>

#include "esp_camera.h"
#include "esp_log.h"

struct ra_esp32_camera {
    esp32s3_camera_board_config_t config;
    bool initialized;
    camera_fb_t *current_fb;
};

static const char *TAG = "ra_esp32_camera";

static int camera_init_if_needed(ra_esp32_camera_t *camera) {
    if (camera->initialized) {
        return 0;
    }
    camera_config_t config = {
        .pin_pwdn = camera->config.pwdn,
        .pin_reset = camera->config.reset,
        .pin_xclk = camera->config.xclk,
        .pin_sccb_sda = camera->config.sccb_sda,
        .pin_sccb_scl = camera->config.sccb_scl,
        .pin_d7 = camera->config.d7,
        .pin_d6 = camera->config.d6,
        .pin_d5 = camera->config.d5,
        .pin_d4 = camera->config.d4,
        .pin_d3 = camera->config.d3,
        .pin_d2 = camera->config.d2,
        .pin_d1 = camera->config.d1,
        .pin_d0 = camera->config.d0,
        .pin_vsync = camera->config.vsync,
        .pin_href = camera->config.href,
        .pin_pclk = camera->config.pclk,
        .xclk_freq_hz = 20000000,
        .ledc_timer = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG,
        .frame_size = FRAMESIZE_VGA,
        .jpeg_quality = 12,
        .fb_count = 1,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
    };
    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "camera.init failed err=%s", esp_err_to_name(err));
        esp_camera_deinit();
        return -1;
    }
    camera->initialized = true;
    ESP_LOGI(TAG, "camera.initialized");
    return 0;
}

static int capture_jpeg(void *ctx, const uint8_t **data, size_t *size) {
    ra_esp32_camera_t *camera = (ra_esp32_camera_t *)ctx;
    if (camera_init_if_needed(camera) != 0) {
        return -1;
    }
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb == NULL || fb->format != PIXFORMAT_JPEG) {
        ESP_LOGE(TAG, "camera.capture failed");
        return -1;
    }
    camera->current_fb = fb;
    *data = fb->buf;
    *size = fb->len;
    ESP_LOGI(TAG, "camera.capture jpeg bytes=%u", (unsigned)fb->len);
    return 0;
}

static void release_jpeg(void *ctx, const uint8_t *data) {
    ra_esp32_camera_t *camera = (ra_esp32_camera_t *)ctx;
    if (data == NULL) {
        return;
    }
    if (camera->current_fb != NULL && camera->current_fb->buf == data) {
        esp_camera_fb_return(camera->current_fb);
        camera->current_fb = NULL;
    }
}

ra_esp32_camera_t *ra_esp32_camera_create(const esp32s3_camera_board_config_t *config) {
    if (config == NULL) {
        return NULL;
    }
    ra_esp32_camera_t *camera = calloc(1, sizeof(*camera));
    if (camera == NULL) {
        return NULL;
    }
    camera->config = *config;
    return camera;
}

void ra_esp32_camera_destroy(ra_esp32_camera_t *camera) {
    if (camera != NULL && camera->initialized) {
        esp_camera_deinit();
    }
    free(camera);
}

ra_camera_source_t ra_esp32_camera_as_source(ra_esp32_camera_t *camera) {
    ra_camera_source_t source = {
        .ctx = camera,
        .codec = "jpeg",
        .capture_jpeg = capture_jpeg,
        .release_jpeg = release_jpeg,
    };
    return source;
}
