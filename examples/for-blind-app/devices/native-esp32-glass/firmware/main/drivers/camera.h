#ifndef CAMERA_H
#define CAMERA_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"
#include "sensor.h"

esp_err_t camera_init(void);
esp_err_t camera_capture_frame(uint8_t **jpg_data, size_t *jpg_len);
esp_err_t camera_return_frame(void);
esp_err_t camera_set_quality(int quality);
esp_err_t camera_set_framesize(framesize_t framesize);
esp_err_t camera_capture_hq(uint8_t **jpg_data, size_t *jpg_len);
void camera_task_start(void);

#endif // CAMERA_H