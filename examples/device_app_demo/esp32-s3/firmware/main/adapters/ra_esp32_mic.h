#ifndef REALTIME_AGENT_ESP32_MIC_H
#define REALTIME_AGENT_ESP32_MIC_H

#include "board/board_config.h"
#include "realtime_agent_device/ra_hardware.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ra_esp32_mic ra_esp32_mic_t;

ra_esp32_mic_t *ra_esp32_mic_create(const esp32s3_mic_board_config_t *config);
void ra_esp32_mic_destroy(ra_esp32_mic_t *mic);
ra_mic_source_t ra_esp32_mic_as_source(ra_esp32_mic_t *mic);

#ifdef __cplusplus
}
#endif

#endif
