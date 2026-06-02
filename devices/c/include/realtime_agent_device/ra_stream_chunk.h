#ifndef REALTIME_AGENT_DEVICE_RA_STREAM_CHUNK_H
#define REALTIME_AGENT_DEVICE_RA_STREAM_CHUNK_H

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
    char user_id[RA_MAX_ID_LEN];
    char session_id[RA_MAX_ID_LEN];
    char stream_id[RA_MAX_ID_LEN];
    char stream_type[RA_MAX_STREAM_TYPE_LEN];
    int seq;
    int64_t timestamp_ms;
    char codec[RA_MAX_CODEC_LEN];
    int sample_rate;
    int channels;
    int duration_ms;
    bool final;
    const char *metadata_json;
    const uint8_t *payload;
    size_t payload_size;
} ra_stream_chunk_t;

void ra_stream_chunk_init(ra_stream_chunk_t *chunk);
int ra_stream_chunk_encode(const ra_stream_chunk_t *chunk, uint8_t *out, size_t capacity, size_t *written);
int ra_stream_chunk_decode(const uint8_t *data, size_t size, ra_stream_chunk_t *chunk, const uint8_t **payload);
int ra_stream_chunk_decode_header_json(const uint8_t *data, size_t size, char *out, size_t capacity, size_t *written);

#ifdef __cplusplus
}
#endif

#endif
