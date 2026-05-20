#ifndef REALTIME_AGENT_STREAM_H
#define REALTIME_AGENT_STREAM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct realtime_agent_stream_header {
    char version[32];
    char user_id[64];
    char session_id[64];
    char stream_id[64];
    char stream_type[32];
    int seq;
    long long timestamp_ms;
    char codec[32];
    int sample_rate;
    int channels;
    int duration_ms;
    int final;
    size_t payload_size;
} realtime_agent_stream_header_t;

int realtime_agent_stream_encode(const char *header_json, const uint8_t *payload, size_t payload_size, uint8_t *out, size_t out_size, size_t *written);
int realtime_agent_stream_decode(const uint8_t *raw, size_t raw_size, char *header_json, size_t header_size, const uint8_t **payload, size_t *payload_size);

#ifdef __cplusplus
}
#endif

#endif
