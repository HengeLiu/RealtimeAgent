#include "realtime_agent_device/ra_stream_chunk.h"

#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

#include "realtime_agent_device/ra_event.h"

static void copy_text(char *dst, size_t capacity, const char *src) {
    if (capacity == 0) {
        return;
    }
    if (src == NULL) {
        src = "";
    }
    strncpy(dst, src, capacity - 1);
    dst[capacity - 1] = '\0';
}

void ra_stream_chunk_init(ra_stream_chunk_t *chunk) {
    if (chunk == NULL) {
        return;
    }
    memset(chunk, 0, sizeof(*chunk));
    copy_text(chunk->version, sizeof(chunk->version), RA_PROTOCOL_VERSION);
    copy_text(chunk->codec, sizeof(chunk->codec), RA_DEFAULT_AUDIO_CODEC);
    chunk->sample_rate = RA_DEFAULT_AUDIO_SAMPLE_RATE;
    chunk->channels = RA_DEFAULT_AUDIO_CHANNELS;
    chunk->duration_ms = RA_DEFAULT_AUDIO_CHUNK_MS;
    chunk->timestamp_ms = ra_now_ms();
    chunk->metadata_json = "{}";
}

static int append_text(char *out, size_t capacity, size_t *pos, const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    int n = vsnprintf(out + *pos, capacity - *pos, fmt, args);
    va_end(args);
    if (n < 0 || (size_t)n >= capacity - *pos) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    *pos += (size_t)n;
    return RA_OK;
}

int ra_stream_chunk_encode(const ra_stream_chunk_t *chunk, uint8_t *out, size_t capacity, size_t *written) {
    if (chunk == NULL || out == NULL || chunk->payload == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    char header[1024];
    size_t pos = 0;
    const char *metadata = chunk->metadata_json == NULL ? "{}" : chunk->metadata_json;
    int rc = append_text(
        header,
        sizeof(header),
        &pos,
        "{\"version\":\"%s\",\"user_id\":\"%s\",\"session_id\":\"%s\",\"stream_id\":\"%s\",\"stream_type\":\"%s\","
        "\"seq\":%d,\"timestamp_ms\":%lld,\"codec\":\"%s\",\"sample_rate\":%d,\"channels\":%d,"
        "\"duration_ms\":%d,\"payload_size\":%zu,\"final\":%s,\"metadata\":%s}",
        chunk->version[0] == '\0' ? RA_PROTOCOL_VERSION : chunk->version,
        chunk->user_id,
        chunk->session_id,
        chunk->stream_id,
        chunk->stream_type,
        chunk->seq,
        (long long)(chunk->timestamp_ms == 0 ? ra_now_ms() : chunk->timestamp_ms),
        chunk->codec,
        chunk->sample_rate,
        chunk->channels,
        chunk->duration_ms,
        chunk->payload_size,
        chunk->final ? "true" : "false",
        metadata
    );
    if (rc != RA_OK) {
        return rc;
    }
    if (capacity < 4 + pos + chunk->payload_size) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    uint32_t header_len = htonl((uint32_t)pos);
    memcpy(out, &header_len, 4);
    memcpy(out + 4, header, pos);
    memcpy(out + 4 + pos, chunk->payload, chunk->payload_size);
    if (written != NULL) {
        *written = 4 + pos + chunk->payload_size;
    }
    return RA_OK;
}

static int json_string_field(const char *json, const char *key, char *out, size_t capacity) {
    ra_event_t event;
    char wrapper[1400];
    int n = snprintf(wrapper, sizeof(wrapper), "{\"event_name\":\"x\",\"user_id\":\"x\",\"producer_id\":\"x\",\"payload\":%s}", json);
    if (n <= 0 || (size_t)n >= sizeof(wrapper)) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    int rc = ra_event_decode_json(wrapper, &event);
    if (rc != RA_OK) {
        return rc;
    }
    return ra_event_extract_payload_string(&event, key, out, capacity);
}

static int json_int_field(const char *json, const char *key, int default_value) {
    ra_event_t event;
    char wrapper[1400];
    int n = snprintf(wrapper, sizeof(wrapper), "{\"event_name\":\"x\",\"user_id\":\"x\",\"producer_id\":\"x\",\"payload\":%s}", json);
    if (n <= 0 || (size_t)n >= sizeof(wrapper)) {
        return default_value;
    }
    if (ra_event_decode_json(wrapper, &event) != RA_OK) {
        return default_value;
    }
    return ra_event_extract_payload_int(&event, key, default_value);
}

static bool json_bool_field(const char *json, const char *key) {
    const char *p = strstr(json, key);
    if (p == NULL) {
        return false;
    }
    p = strchr(p, ':');
    if (p == NULL) {
        return false;
    }
    return strstr(p, "true") == p + 1 || strstr(p, " true") == p + 1;
}

int ra_stream_chunk_decode_header_json(const uint8_t *data, size_t size, char *out, size_t capacity, size_t *written) {
    if (data == NULL || size < 4 || out == NULL || capacity == 0) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    uint32_t header_len = 0;
    memcpy(&header_len, data, 4);
    header_len = ntohl(header_len);
    if (header_len == 0 || 4 + (size_t)header_len > size) {
        return RA_ERROR_PARSE_FAILED;
    }
    if ((size_t)header_len + 1 > capacity) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    memcpy(out, data + 4, header_len);
    out[header_len] = '\0';
    if (written != NULL) {
        *written = header_len;
    }
    return RA_OK;
}

int ra_stream_chunk_decode(const uint8_t *data, size_t size, ra_stream_chunk_t *chunk, const uint8_t **payload) {
    if (data == NULL || chunk == NULL || payload == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    char header[1024];
    size_t header_len = 0;
    int rc = ra_stream_chunk_decode_header_json(data, size, header, sizeof(header), &header_len);
    if (rc != RA_OK) {
        return rc;
    }
    (void)header_len;
    ra_stream_chunk_init(chunk);
    if (json_string_field(header, "user_id", chunk->user_id, sizeof(chunk->user_id)) != RA_OK ||
        json_string_field(header, "session_id", chunk->session_id, sizeof(chunk->session_id)) != RA_OK ||
        json_string_field(header, "stream_id", chunk->stream_id, sizeof(chunk->stream_id)) != RA_OK ||
        json_string_field(header, "stream_type", chunk->stream_type, sizeof(chunk->stream_type)) != RA_OK ||
        json_string_field(header, "codec", chunk->codec, sizeof(chunk->codec)) != RA_OK) {
        return RA_ERROR_PARSE_FAILED;
    }
    (void)json_string_field(header, "version", chunk->version, sizeof(chunk->version));
    chunk->seq = json_int_field(header, "seq", 0);
    chunk->timestamp_ms = json_int_field(header, "timestamp_ms", 0);
    chunk->sample_rate = json_int_field(header, "sample_rate", 0);
    chunk->channels = json_int_field(header, "channels", 0);
    chunk->duration_ms = json_int_field(header, "duration_ms", 0);
    chunk->payload_size = (size_t)json_int_field(header, "payload_size", -1);
    chunk->final = json_bool_field(header, "\"final\"");

    uint32_t raw_header_len = 0;
    memcpy(&raw_header_len, data, 4);
    raw_header_len = ntohl(raw_header_len);
    size_t actual_payload_size = size - 4 - raw_header_len;
    if (chunk->payload_size != actual_payload_size) {
        return RA_ERROR_PARSE_FAILED;
    }
    *payload = data + 4 + raw_header_len;
    chunk->payload = *payload;
    return RA_OK;
}
