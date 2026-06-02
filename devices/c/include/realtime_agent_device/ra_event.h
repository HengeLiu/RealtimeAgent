#ifndef REALTIME_AGENT_DEVICE_RA_EVENT_H
#define REALTIME_AGENT_DEVICE_RA_EVENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "ra_config.h"
#include "ra_error.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    char version[32];
    char event_id[RA_MAX_ID_LEN];
    char event_name[RA_MAX_EVENT_NAME_LEN];
    int64_t timestamp_ms;
    char user_id[RA_MAX_ID_LEN];
    char producer_id[RA_MAX_ID_LEN];
    char session_id[RA_MAX_ID_LEN];
    char stream_id[RA_MAX_ID_LEN];
    char stream_type[RA_MAX_STREAM_TYPE_LEN];
    const char *payload_json;
} ra_event_t;

void ra_event_init(
    ra_event_t *event,
    const char *event_name,
    const char *user_id,
    const char *producer_id,
    const char *payload_json
);

int ra_event_encode_json(const ra_event_t *event, char *out, size_t capacity, size_t *written);
int ra_event_decode_json(const char *json, ra_event_t *event);
bool ra_event_payload_contains(const ra_event_t *event, const char *needle);
int ra_event_extract_payload_string(const ra_event_t *event, const char *key, char *out, size_t capacity);
int ra_event_extract_payload_int(const ra_event_t *event, const char *key, int default_value);
int64_t ra_now_ms(void);

#ifdef __cplusplus
}
#endif

#endif
