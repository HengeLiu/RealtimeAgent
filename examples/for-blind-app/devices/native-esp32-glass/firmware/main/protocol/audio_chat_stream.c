#include "audio_chat_stream.h"
#include <string.h>
#include <sys/time.h>

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

static uint64_t get_timestamp_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

// Stream sequence counter
static uint32_t s_seq_audio = 0;
static uint32_t s_seq_camera = 0;
static uint32_t s_seq_imu = 0;

int audio_chat_stream_encode(const char *stream_id, const char *stream_type,
                              const uint8_t *payload, size_t payload_size,
                              const char *user_id, const char *session_id,
                              uint8_t *out, size_t out_size, size_t *written)
{
    return audio_chat_stream_encode_ex(stream_id, stream_type, payload, payload_size,
                                        user_id, session_id, false, NULL, out, out_size, written);
}

int audio_chat_stream_encode_ex(const char *stream_id, const char *stream_type,
                                 const uint8_t *payload, size_t payload_size,
                                 const char *user_id, const char *session_id,
                                 bool final, const char *metadata_json,
                                 uint8_t *out, size_t out_size, size_t *written)
{
    uint32_t seq = 0;
    const char *codec = "pcm16le";

    if (strcmp(stream_type, "sensor.rgb") == 0) {
        seq = s_seq_camera++;
        codec = "jpeg";
    } else if (strcmp(stream_type, "sensor.imu") == 0) {
        seq = s_seq_imu++;
    } else {
        // sensor.mic and any other type: use global audio counter
        seq = s_seq_audio++;
    }

    uint64_t timestamp_ms = get_timestamp_ms();

    // Build header JSON per audio-chat-stream.schema.json
    char header_json[512];
    int header_len;
    if (metadata_json && metadata_json[0]) {
        header_len = snprintf(header_json, sizeof(header_json),
            "{"
            "\"version\":\"audio-chat.v1\","
            "\"user_id\":\"%s\","
            "\"session_id\":\"%s\","
            "\"stream_id\":\"%s\","
            "\"stream_type\":\"%s\","
            "\"seq\":%lu,"
            "\"timestamp_ms\":%llu,"
            "\"codec\":\"%s\","
            "\"sample_rate\":16000,"
            "\"channels\":1,"
            "\"duration_ms\":20,"
            "\"payload_size\":%u,"
            "\"final\":%s,"
            "\"metadata\":%s"
            "}",
            user_id ? user_id : "",
            session_id ? session_id : "",
            stream_id,
            stream_type,
            seq,
            (unsigned long long)timestamp_ms,
            codec,
            (unsigned)payload_size,
            final ? "true" : "false",
            metadata_json);
    } else {
        header_len = snprintf(header_json, sizeof(header_json),
            "{"
            "\"version\":\"audio-chat.v1\","
            "\"user_id\":\"%s\","
            "\"session_id\":\"%s\","
            "\"stream_id\":\"%s\","
            "\"stream_type\":\"%s\","
            "\"seq\":%lu,"
            "\"timestamp_ms\":%llu,"
            "\"codec\":\"%s\","
            "\"sample_rate\":16000,"
            "\"channels\":1,"
            "\"duration_ms\":20,"
            "\"payload_size\":%u,"
            "\"final\":%s"
            "}",
            user_id ? user_id : "",
            session_id ? session_id : "",
            stream_id,
            stream_type,
            seq,
            (unsigned long long)timestamp_ms,
            codec,
            (unsigned)payload_size,
            final ? "true" : "false");
    }

    if (header_len < 0 || (size_t)header_len >= sizeof(header_json)) {
        return -1;
    }

    size_t total = 4 + header_len + payload_size;
    if (total > out_size) {
        return -1;
    }

    write_u32_be(out, (uint32_t)header_len);
    memcpy(out + 4, header_json, header_len);
    if (payload_size > 0) {
        memcpy(out + 4 + header_len, payload, payload_size);
    }

    if (written != NULL) {
        *written = total;
    }
    return 0;
}

int audio_chat_stream_decode(const uint8_t *raw, size_t raw_size,
                              char *header_json, size_t header_size,
                              const uint8_t **payload, size_t *payload_size)
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

void audio_chat_stream_reset_seq(void) {
    s_seq_audio = 0;
    s_seq_camera = 0;
    s_seq_imu = 0;
}

int audio_chat_stream_encode_imu(const char *user_id, const char *session_id,
                                const char *imu_json, size_t imu_json_len,
                                uint8_t *out, size_t out_size, size_t *written)
{
    uint64_t timestamp_ms = get_timestamp_ms();

    // Build header JSON for IMU stream
    char header_json[512];
    int header_len = snprintf(header_json, sizeof(header_json),
        "{"
        "\"version\":\"audio-chat.v1\","
        "\"user_id\":\"%s\","
        "\"session_id\":\"%s\","
        "\"stream_id\":\"imu\","
        "\"stream_type\":\"sensor.imu\","
        "\"seq\":%lu,"
        "\"timestamp_ms\":%llu,"
        "\"codec\":\"json\","
        "\"sample_rate\":100,"
        "\"channels\":1,"
        "\"duration_ms\":10,"
        "\"payload_size\":%u,"
        "\"final\":false"
        "}",
        user_id ? user_id : "",
        session_id ? session_id : "",
        s_seq_imu++,
        (unsigned long long)timestamp_ms,
        (unsigned)imu_json_len);

    if (header_len < 0 || (size_t)header_len >= sizeof(header_json)) {
        return -1;
    }

    size_t total = 4 + header_len + imu_json_len;
    if (total > out_size) {
        return -1;
    }

    write_u32_be(out, (uint32_t)header_len);
    memcpy(out + 4, header_json, header_len);
    memcpy(out + 4 + header_len, imu_json, imu_json_len);

    if (written != NULL) {
        *written = total;
    }
    return 0;
}