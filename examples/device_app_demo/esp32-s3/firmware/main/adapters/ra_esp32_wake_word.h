#ifndef REALTIME_AGENT_ESP32_WAKE_WORD_H
#define REALTIME_AGENT_ESP32_WAKE_WORD_H

#include "realtime_agent_device/ra_client.h"

#ifdef __cplusplus
extern "C" {
#endif

void ra_esp32_wake_word_start(ra_device_client_t *client);

#ifdef __cplusplus
}
#endif

#endif
