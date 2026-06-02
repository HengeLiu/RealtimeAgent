#ifndef REALTIME_AGENT_ESP32_TRANSPORT_H
#define REALTIME_AGENT_ESP32_TRANSPORT_H

#include "realtime_agent_device/ra_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ra_esp32_transport ra_esp32_transport_t;

ra_esp32_transport_t *ra_esp32_transport_create(const char *server_url, const char *device_id);
void ra_esp32_transport_destroy(ra_esp32_transport_t *transport);
ra_transport_t ra_esp32_transport_as_sdk_transport(ra_esp32_transport_t *transport);

#ifdef __cplusplus
}
#endif

#endif
