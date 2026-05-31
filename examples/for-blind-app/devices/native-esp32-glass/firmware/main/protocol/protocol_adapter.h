#ifndef PROTOCOL_ADAPTER_H
#define PROTOCOL_ADAPTER_H

#include <stddef.h>

typedef void (*on_event_callback_t)(const char *event_name, const char *payload, size_t len);

char* protocol_adapter_create_event(const char *event_name, const char *payload, size_t payload_len,
                                     const char *user_id, const char *device_id, const char *session_id,
                                     const char *stream_id, const char *stream_type);
void protocol_adapter_parse_event(const char *json_str, size_t len, on_event_callback_t callback);

#endif // PROTOCOL_ADAPTER_H