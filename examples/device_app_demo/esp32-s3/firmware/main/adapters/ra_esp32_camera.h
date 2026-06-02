#ifndef REALTIME_AGENT_ESP32_CAMERA_H
#define REALTIME_AGENT_ESP32_CAMERA_H

#include "board/board_config.h"
#include "realtime_agent_device/ra_hardware.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ra_esp32_camera ra_esp32_camera_t;

ra_esp32_camera_t *ra_esp32_camera_create(const esp32s3_camera_board_config_t *config);
void ra_esp32_camera_destroy(ra_esp32_camera_t *camera);
ra_camera_source_t ra_esp32_camera_as_source(ra_esp32_camera_t *camera);

#ifdef __cplusplus
}
#endif

#endif
