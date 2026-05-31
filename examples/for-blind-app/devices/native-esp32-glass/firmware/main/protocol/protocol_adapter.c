#include "protocol_adapter.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdbool.h>
#include "esp_log.h"

static const char *TAG = "proto";

static const char *PROTOCOL_VERSION = "audio-chat.v1";

static uint64_t get_timestamp_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (uint64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

char* protocol_adapter_create_event(const char *event_name, const char *payload, size_t payload_len,
                                     const char *user_id, const char *device_id, const char *session_id,
                                     const char *stream_id, const char *stream_type) {
    static uint64_t event_counter = 0;
    event_counter++;

    char *json = malloc(1024 + payload_len);
    if (!json) return NULL;

    uint64_t timestamp = get_timestamp_ms();
    uint64_t event_id_num = (timestamp << 16) | (event_counter & 0xFFFF);

    char event_id[64];
    snprintf(event_id, sizeof(event_id), "evt_%llx", (unsigned long long)event_id_num);

    int n;
    if (stream_id && stream_type) {
        n = snprintf(json, 1024 + payload_len,
            "{"
            "\"version\":\"%s\","
            "\"event_id\":\"%s\","
            "\"event_name\":\"%s\","
            "\"timestamp_ms\":%llu,"
            "\"user_id\":\"%s\","
            "\"producer_id\":\"%s\","
            "\"session_id\":\"%s\","
            "\"stream_id\":\"%s\","
            "\"stream_type\":\"%s\","
            "\"payload\":%.*s"
            "}",
            PROTOCOL_VERSION,
            event_id,
            event_name,
            (unsigned long long)timestamp,
            user_id ? user_id : "",
            device_id ? device_id : "",
            session_id ? session_id : "",
            stream_id,
            stream_type,
            (int)payload_len,
            payload ? payload : "{}"
        );
    } else {
        n = snprintf(json, 1024 + payload_len,
            "{"
            "\"version\":\"%s\","
            "\"event_id\":\"%s\","
            "\"event_name\":\"%s\","
            "\"timestamp_ms\":%llu,"
            "\"user_id\":\"%s\","
            "\"producer_id\":\"%s\","
            "\"session_id\":\"%s\","
            "\"payload\":%.*s"
            "}",
            PROTOCOL_VERSION,
            event_id,
            event_name,
            (unsigned long long)timestamp,
            user_id ? user_id : "",
            device_id ? device_id : "",
            session_id ? session_id : "",
            (int)payload_len,
            payload ? payload : "{}"
        );
    }

    if (n <= 0) {
        free(json);
        return NULL;
    }

    return json;
}

void protocol_adapter_parse_event(const char *json_str, size_t len, on_event_callback_t callback) {
    if (!json_str || len == 0 || !callback) return;

    // Make a null-terminated copy
    char *json = malloc(len + 1);
    if (!json) return;
    memcpy(json, json_str, len);
    json[len] = '\0';

    // Find event_name
    const char *en_start = strstr(json, "\"event_name\"");
    const char *p_start = strstr(json, "\"payload\"");

    if (en_start && p_start) {
        en_start += strlen("\"event_name\"");
        while (*en_start == ' ' || *en_start == ':') en_start++;
        if (*en_start == '"') en_start++;

        const char *en_end = strchr(en_start, '"');
        if (en_end) {
            char event_name[128];
            size_t en_len = en_end - en_start;
            if (en_len >= sizeof(event_name)) en_len = sizeof(event_name) - 1;
            memcpy(event_name, en_start, en_len);
            event_name[en_len] = '\0';

            // Find payload start
            p_start += strlen("\"payload\"");
            while (*p_start == ' ' || *p_start == ':') p_start++;

            // Find payload end (matching braces)
            int brace = 0;
            const char *p_end = p_start;
            bool in_str = false;
            while (*p_end) {
                if (*p_end == '"' && (p_end == p_start || *(p_end-1) != '\\')) {
                    in_str = !in_str;
                } else if (!in_str) {
                    if (*p_end == '{') brace++;
                    else if (*p_end == '}') {
                        brace--;
                        if (brace == 0) { p_end++; break; }
                    }
                }
                p_end++;
            }

            size_t payload_len = p_end - p_start;
            callback(event_name, p_start, payload_len);
        }
    }

    free(json);
}
