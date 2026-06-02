#ifndef REALTIME_AGENT_DEVICE_RA_DIAGNOSTICS_H
#define REALTIME_AGENT_DEVICE_RA_DIAGNOSTICS_H

#include <stdbool.h>
#include <stdint.h>

#include "ra_config.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool registered;
    char connection_state[32];
    char conversation_state[32];
    uint32_t sent_events;
    uint32_t received_events;
    uint32_t sent_stream_chunks;
    uint32_t received_output_chunks;
    uint32_t speaker_buffered_ms;
    uint32_t speaker_buffered_bytes;
    uint32_t speaker_out_of_order_chunks;
    uint32_t speaker_duplicate_chunks;
    char last_event_name[RA_MAX_EVENT_NAME_LEN];
    char last_error[RA_MAX_ERROR_LEN];
} ra_diagnostics_t;

void ra_diagnostics_init(ra_diagnostics_t *diagnostics);
void ra_diagnostics_set_error(ra_diagnostics_t *diagnostics, const char *error);

#ifdef __cplusplus
}
#endif

#endif
