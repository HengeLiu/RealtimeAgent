#include "realtime_agent_device/realtime_agent_stream.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t read_u32_be(const uint8_t *raw)
{
    return ((uint32_t)raw[0] << 24) | ((uint32_t)raw[1] << 16) | ((uint32_t)raw[2] << 8) | (uint32_t)raw[3];
}

static void write_u32_be(uint8_t *raw, uint32_t value)
{
    raw[0] = (uint8_t)((value >> 24) & 0xff);
    raw[1] = (uint8_t)((value >> 16) & 0xff);
    raw[2] = (uint8_t)((value >> 8) & 0xff);
    raw[3] = (uint8_t)(value & 0xff);
}

static int extract_payload_size(const char *header_json, size_t *payload_size)
{
    const char *key = strstr(header_json, "\"payload_size\"");
    if (key == NULL) {
        return -1;
    }
    const char *colon = strchr(key, ':');
    if (colon == NULL) {
        return -1;
    }
    char *end = NULL;
    unsigned long value = strtoul(colon + 1, &end, 10);
    if (end == colon + 1) {
        return -1;
    }
    *payload_size = (size_t)value;
    return 0;
}

int realtime_agent_stream_encode(const char *header_json, const uint8_t *payload, size_t payload_size, uint8_t *out, size_t out_size, size_t *written)
{
    size_t header_len = strlen(header_json);
    if (header_len == 0 || header_len > UINT32_MAX || out_size < 4 + header_len + payload_size) {
        return -1;
    }
    write_u32_be(out, (uint32_t)header_len);
    memcpy(out + 4, header_json, header_len);
    if (payload_size > 0) {
        memcpy(out + 4 + header_len, payload, payload_size);
    }
    if (written != NULL) {
        *written = 4 + header_len + payload_size;
    }
    return 0;
}

int realtime_agent_stream_decode(const uint8_t *raw, size_t raw_size, char *header_json, size_t header_size, const uint8_t **payload, size_t *payload_size)
{
    if (raw_size < 4) {
        return -1;
    }
    uint32_t header_len = read_u32_be(raw);
    size_t header_end = 4 + (size_t)header_len;
    if (header_len == 0 || header_end > raw_size || header_size <= header_len) {
        return -1;
    }
    memcpy(header_json, raw + 4, header_len);
    header_json[header_len] = '\0';
    *payload = raw + header_end;
    *payload_size = raw_size - header_end;
    size_t expected = 0;
    if (extract_payload_size(header_json, &expected) != 0 || expected != *payload_size) {
        return -1;
    }
    return 0;
}
