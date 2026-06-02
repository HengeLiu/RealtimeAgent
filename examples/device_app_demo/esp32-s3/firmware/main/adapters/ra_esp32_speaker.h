#ifndef REALTIME_AGENT_ESP32_SPEAKER_H
#define REALTIME_AGENT_ESP32_SPEAKER_H

#include "board/board_config.h"
#include "realtime_agent_device/ra_hardware.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ra_esp32_speaker ra_esp32_speaker_t;

ra_esp32_speaker_t *ra_esp32_speaker_create(const esp32s3_speaker_board_config_t *config);
void ra_esp32_speaker_destroy(ra_esp32_speaker_t *speaker);
ra_speaker_sink_t ra_esp32_speaker_as_sink(ra_esp32_speaker_t *speaker);

#ifdef __cplusplus
}
#endif

#endif
