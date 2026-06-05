#include "protocol_adapter.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdbool.h>
#include "esp_log.h"

static const char *TAG = "protocol_adapter";

static const char *PROTOCOL_VERSION = "realtime-agent.v1";

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

    char *json = malloc(512 + payload_len + 128);
    if (!json) return NULL;

    uint64_t timestamp = get_timestamp_ms();
    uint64_t event_id_num = (timestamp << 16) | (event_counter & 0xFFFF);

    char event_id[64];
    snprintf(event_id, sizeof(event_id), "evt_%llx", (unsigned long long)event_id_num);

    char stream_id_json[128];
    char stream_type_json[128];
    
    if (stream_id) {
        snprintf(stream_id_json, sizeof(stream_id_json), "\"%s\"", stream_id);
    } else {
        snprintf(stream_id_json, sizeof(stream_id_json), "null");
    }
    
    if (stream_type) {
        snprintf(stream_type_json, sizeof(stream_type_json), "\"%s\"", stream_type);
    } else {
        snprintf(stream_type_json, sizeof(stream_type_json), "null");
    }

    int n = snprintf(json, 512 + payload_len + 128,
        "{"
        "\"version\":\"%s\","
        "\"event_id\":\"%s\","
        "\"event_name\":\"%s\","
        "\"timestamp_ms\":%llu,"
        "\"user_id\":\"%s\","
        "\"producer_id\":\"%s\","
        "\"session_id\":\"%s\","
        "\"stream_id\":%s,"
        "\"stream_type\":%s,"
        "\"payload\":%.*s"
        "}",
        PROTOCOL_VERSION,
        event_id,
        event_name,
        (unsigned long long)timestamp,
        user_id ? user_id : "",
        device_id ? device_id : "",
        session_id ? session_id : "",
        stream_id_json,
        stream_type_json,
        (int)payload_len,
        payload ? payload : "{}"
    );

    if (n <= 0) {
        free(json);
        return NULL;
    }

    return json;
}

static int hex_to_int(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return 0;
}

static void decode_json_string(const char *src, char *dst, size_t dst_size) {
    size_t j = 0;
    for (size_t i = 0; i < strlen(src) && j < dst_size - 1; i++) {
        if (src[i] == '\\' && src[i+1] == 'u' && i + 5 < strlen(src)) {
            int hi = hex_to_int(src[i+2]);
            int lo = hex_to_int(src[i+3]);
            dst[j++] = (char)((hi << 4) | lo);
            i += 5;
        } else if (src[i] == '\\' && src[i+1] == 'n') {
            dst[j++] = '\n';
            i++;
        } else if (src[i] == '\\' && src[i+1] == '"') {
            dst[j++] = '"';
            i++;
        } else if (src[i] == '\\' && src[i+1] == '\\') {
            dst[j++] = '\\';
            i++;
        } else {
            dst[j++] = src[i];
        }
    }
    dst[j] = '\0';
}

void protocol_adapter_parse_event(const char *json_str, size_t len, on_event_callback_t callback) {
    ESP_LOGI(TAG, "parse_event called: json_str=%p, len=%zu, callback=%p", json_str, len, callback);
    
    if (!json_str || len == 0) {
        ESP_LOGW(TAG, "Invalid input");
        return;
    }

    char *json = (char *)json_str;
    if (len < (size_t)-1) {
        json = malloc(len + 1);
        if (!json) {
            ESP_LOGE(TAG, "Failed to allocate memory");
            return;
        }
        memcpy(json, json_str, len);
        json[len] = '\0';
    }

    ESP_LOGI(TAG, "Parsing JSON: %s", json);

    const char *event_name_start = strstr(json, "\"event_name\"");
    const char *payload_start = strstr(json, "\"payload\"");

    ESP_LOGI(TAG, "event_name_start=%p, payload_start=%p", event_name_start, payload_start);

    if (event_name_start && payload_start) {
        event_name_start += strlen("\"event_name\"");
        while (*event_name_start && (*event_name_start == ' ' || *event_name_start == ':')) {
            event_name_start++;
        }
        if (*event_name_start == '"') {
            event_name_start++;
        }
        
        const char *event_name_end = strchr(event_name_start, '"');
        if (event_name_end) {
            char event_name[128];
            size_t en_len = event_name_end - event_name_start;
            if (en_len >= sizeof(event_name)) en_len = sizeof(event_name) - 1;
            strncpy(event_name, event_name_start, en_len);
            event_name[en_len] = '\0';

            ESP_LOGI(TAG, "Extracted event_name: %s", event_name);

            payload_start += strlen("\"payload\":");
            
            int brace_count = 0;
            const char *payload_end = payload_start;
            bool in_string = false;
            
            while (*payload_end) {
                if (*payload_end == '"' && (payload_end == payload_start || *(payload_end - 1) != '\\')) {
                    in_string = !in_string;
                } else if (!in_string) {
                    if (*payload_end == '{') brace_count++;
                    else if (*payload_end == '}') {
                        brace_count--;
                        if (brace_count == 0) {
                            payload_end++;
                            break;
                        }
                    }
                }
                payload_end++;
            }
            
            size_t payload_len = payload_end - payload_start;
            char *payload_copy = malloc(payload_len + 1);
            if (payload_copy) {
                strncpy(payload_copy, payload_start, payload_len);
                payload_copy[payload_len] = '\0';
                
                ESP_LOGI(TAG, "Extracted payload (len=%zu): %s", payload_len, payload_copy);
                ESP_LOGI(TAG, "Calling callback...");
                
                if (callback) {
                    callback(event_name, payload_copy, payload_len);
                    ESP_LOGI(TAG, "Callback returned");
                } else {
                    ESP_LOGW(TAG, "Callback is NULL!");
                }
                
                free(payload_copy);
            } else {
                ESP_LOGE(TAG, "Failed to allocate payload memory");
            }
        } else {
            ESP_LOGW(TAG, "Failed to find event_name end quote");
        }
    } else {
        ESP_LOGW(TAG, "Failed to find event_name or payload in JSON");
    }

    if (json != json_str) {
        free(json);
    }
}