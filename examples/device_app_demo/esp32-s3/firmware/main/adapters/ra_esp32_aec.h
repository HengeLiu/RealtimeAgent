#ifndef REALTIME_AGENT_ESP32_AEC_H
#define REALTIME_AGENT_ESP32_AEC_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void ra_esp32_aec_record_reference(const uint8_t *pcm, size_t size);
void ra_esp32_aec_record_output(const uint8_t *pcm, size_t size);

#ifdef __cplusplus
}
#endif

#endif
