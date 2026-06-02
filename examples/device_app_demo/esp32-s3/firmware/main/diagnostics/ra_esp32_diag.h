#ifndef REALTIME_AGENT_ESP32_DIAG_H
#define REALTIME_AGENT_ESP32_DIAG_H

#include "realtime_agent_device/ra_diagnostics.h"

#ifdef __cplusplus
extern "C" {
#endif

void ra_esp32_diag_log_snapshot(const ra_diagnostics_t *diagnostics);

#ifdef __cplusplus
}
#endif

#endif
