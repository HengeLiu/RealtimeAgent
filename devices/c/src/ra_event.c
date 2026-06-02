#include "realtime_agent_device/ra_event.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

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

int64_t ra_now_ms(void) {
    return (int64_t)time(NULL) * 1000;
}

static int json_escape(const char *src, char *out, size_t capacity, size_t *written) {
    size_t pos = 0;
    if (src == NULL) {
        src = "";
    }
    for (size_t i = 0; src[i] != '\0'; ++i) {
        char ch = src[i];
        const char *replacement = NULL;
        if (ch == '"') {
            replacement = "\\\"";
        } else if (ch == '\\') {
            replacement = "\\\\";
        } else if (ch == '\n') {
            replacement = "\\n";
        } else if (ch == '\r') {
            replacement = "\\r";
        } else if (ch == '\t') {
            replacement = "\\t";
        }

        if (replacement != NULL) {
            size_t len = strlen(replacement);
            if (pos + len >= capacity) {
                return RA_ERROR_BUFFER_TOO_SMALL;
            }
            memcpy(out + pos, replacement, len);
            pos += len;
        } else {
            if (pos + 1 >= capacity) {
                return RA_ERROR_BUFFER_TOO_SMALL;
            }
            out[pos++] = ch;
        }
    }
    if (pos >= capacity) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    out[pos] = '\0';
    if (written != NULL) {
        *written = pos;
    }
    return RA_OK;
}

static const char *find_json_key(const char *json, const char *key) {
    char pattern[96];
    int n = snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    if (n <= 0 || (size_t)n >= sizeof(pattern)) {
        return NULL;
    }
    return strstr(json, pattern);
}

static const char *skip_ws(const char *p) {
    while (p != NULL && *p != '\0' && isspace((unsigned char)*p)) {
        ++p;
    }
    return p;
}

static int extract_json_string(const char *json, const char *key, char *out, size_t capacity) {
    const char *p = find_json_key(json, key);
    if (p == NULL) {
        return RA_ERROR_NOT_FOUND;
    }
    p = strchr(p, ':');
    if (p == NULL) {
        return RA_ERROR_PARSE_FAILED;
    }
    p = skip_ws(p + 1);
    if (p == NULL || *p != '"') {
        return RA_ERROR_PARSE_FAILED;
    }
    ++p;
    size_t pos = 0;
    while (*p != '\0' && *p != '"') {
        char ch = *p++;
        if (ch == '\\' && *p != '\0') {
            char escaped = *p++;
            switch (escaped) {
                case 'n':
                    ch = '\n';
                    break;
                case 'r':
                    ch = '\r';
                    break;
                case 't':
                    ch = '\t';
                    break;
                default:
                    ch = escaped;
                    break;
            }
        }
        if (pos + 1 >= capacity) {
            return RA_ERROR_BUFFER_TOO_SMALL;
        }
        out[pos++] = ch;
    }
    if (*p != '"') {
        return RA_ERROR_PARSE_FAILED;
    }
    if (capacity == 0) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    out[pos] = '\0';
    return RA_OK;
}

static int64_t extract_json_int64(const char *json, const char *key, int64_t default_value) {
    const char *p = find_json_key(json, key);
    if (p == NULL) {
        return default_value;
    }
    p = strchr(p, ':');
    if (p == NULL) {
        return default_value;
    }
    p = skip_ws(p + 1);
    if (p == NULL) {
        return default_value;
    }
    return (int64_t)strtoll(p, NULL, 10);
}

static int extract_payload_object(const char *json, const char **start, size_t *length) {
    const char *p = find_json_key(json, "payload");
    if (p == NULL) {
        *start = "{}";
        *length = 2;
        return RA_OK;
    }
    p = strchr(p, ':');
    if (p == NULL) {
        return RA_ERROR_PARSE_FAILED;
    }
    p = skip_ws(p + 1);
    if (p == NULL || *p != '{') {
        return RA_ERROR_PARSE_FAILED;
    }
    int depth = 0;
    bool in_string = false;
    bool escaped = false;
    const char *q = p;
    while (*q != '\0') {
        char ch = *q;
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (ch == '\\') {
                escaped = true;
            } else if (ch == '"') {
                in_string = false;
            }
        } else {
            if (ch == '"') {
                in_string = true;
            } else if (ch == '{') {
                depth++;
            } else if (ch == '}') {
                depth--;
                if (depth == 0) {
                    *start = p;
                    *length = (size_t)(q - p + 1);
                    return RA_OK;
                }
            }
        }
        ++q;
    }
    return RA_ERROR_PARSE_FAILED;
}

void ra_event_init(
    ra_event_t *event,
    const char *event_name,
    const char *user_id,
    const char *producer_id,
    const char *payload_json
) {
    if (event == NULL) {
        return;
    }
    memset(event, 0, sizeof(*event));
    copy_text(event->version, sizeof(event->version), RA_PROTOCOL_VERSION);
    snprintf(event->event_id, sizeof(event->event_id), "evt_%lld", (long long)ra_now_ms());
    copy_text(event->event_name, sizeof(event->event_name), event_name);
    event->timestamp_ms = ra_now_ms();
    copy_text(event->user_id, sizeof(event->user_id), user_id);
    copy_text(event->producer_id, sizeof(event->producer_id), producer_id);
    event->payload_json = payload_json == NULL ? "{}" : payload_json;
}

int ra_event_encode_json(const ra_event_t *event, char *out, size_t capacity, size_t *written) {
    if (event == NULL || out == NULL || capacity == 0) {
        return RA_ERROR_INVALID_ARGUMENT;
    }

    char version[96];
    char event_id[RA_MAX_ID_LEN * 2];
    char event_name[RA_MAX_EVENT_NAME_LEN * 2];
    char user_id[RA_MAX_ID_LEN * 2];
    char producer_id[RA_MAX_ID_LEN * 2];
    json_escape(event->version[0] == '\0' ? RA_PROTOCOL_VERSION : event->version, version, sizeof(version), NULL);
    json_escape(event->event_id, event_id, sizeof(event_id), NULL);
    json_escape(event->event_name, event_name, sizeof(event_name), NULL);
    json_escape(event->user_id, user_id, sizeof(user_id), NULL);
    json_escape(event->producer_id, producer_id, sizeof(producer_id), NULL);

    const char *payload = event->payload_json == NULL ? "{}" : event->payload_json;
    int n = snprintf(
        out,
        capacity,
        "{\"version\":\"%s\",\"event_id\":\"%s\",\"event_name\":\"%s\",\"timestamp_ms\":%lld,\"user_id\":\"%s\",\"producer_id\":\"%s\"",
        version,
        event_id,
        event_name,
        (long long)(event->timestamp_ms == 0 ? ra_now_ms() : event->timestamp_ms),
        user_id,
        producer_id
    );
    if (n < 0 || (size_t)n >= capacity) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    size_t pos = (size_t)n;
    if (event->session_id[0] != '\0') {
        n = snprintf(out + pos, capacity - pos, ",\"session_id\":\"%s\"", event->session_id);
        if (n < 0 || (size_t)n >= capacity - pos) {
            return RA_ERROR_BUFFER_TOO_SMALL;
        }
        pos += (size_t)n;
    }
    if (event->stream_id[0] != '\0') {
        n = snprintf(out + pos, capacity - pos, ",\"stream_id\":\"%s\"", event->stream_id);
        if (n < 0 || (size_t)n >= capacity - pos) {
            return RA_ERROR_BUFFER_TOO_SMALL;
        }
        pos += (size_t)n;
    }
    if (event->stream_type[0] != '\0') {
        n = snprintf(out + pos, capacity - pos, ",\"stream_type\":\"%s\"", event->stream_type);
        if (n < 0 || (size_t)n >= capacity - pos) {
            return RA_ERROR_BUFFER_TOO_SMALL;
        }
        pos += (size_t)n;
    }
    n = snprintf(out + pos, capacity - pos, ",\"payload\":%s}", payload);
    if (n < 0 || (size_t)n >= capacity - pos) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    pos += (size_t)n;
    if (written != NULL) {
        *written = pos;
    }
    return RA_OK;
}

int ra_event_decode_json(const char *json, ra_event_t *event) {
    if (json == NULL || event == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    memset(event, 0, sizeof(*event));
    copy_text(event->version, sizeof(event->version), RA_PROTOCOL_VERSION);
    int rc = extract_json_string(json, "event_name", event->event_name, sizeof(event->event_name));
    if (rc != RA_OK) {
        return rc;
    }
    rc = extract_json_string(json, "user_id", event->user_id, sizeof(event->user_id));
    if (rc != RA_OK) {
        return rc;
    }
    rc = extract_json_string(json, "producer_id", event->producer_id, sizeof(event->producer_id));
    if (rc != RA_OK) {
        return rc;
    }
    (void)extract_json_string(json, "version", event->version, sizeof(event->version));
    (void)extract_json_string(json, "event_id", event->event_id, sizeof(event->event_id));
    (void)extract_json_string(json, "session_id", event->session_id, sizeof(event->session_id));
    (void)extract_json_string(json, "stream_id", event->stream_id, sizeof(event->stream_id));
    (void)extract_json_string(json, "stream_type", event->stream_type, sizeof(event->stream_type));
    event->timestamp_ms = extract_json_int64(json, "timestamp_ms", 0);
    const char *payload_start = NULL;
    size_t payload_len = 0;
    rc = extract_payload_object(json, &payload_start, &payload_len);
    if (rc != RA_OK) {
        return rc;
    }
    static char payload_copy[2048];
    if (payload_len >= sizeof(payload_copy)) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    memcpy(payload_copy, payload_start, payload_len);
    payload_copy[payload_len] = '\0';
    event->payload_json = payload_copy;
    return RA_OK;
}

bool ra_event_payload_contains(const ra_event_t *event, const char *needle) {
    if (event == NULL || event->payload_json == NULL || needle == NULL) {
        return false;
    }
    return strstr(event->payload_json, needle) != NULL;
}

int ra_event_extract_payload_string(const ra_event_t *event, const char *key, char *out, size_t capacity) {
    if (event == NULL || event->payload_json == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    return extract_json_string(event->payload_json, key, out, capacity);
}

int ra_event_extract_payload_int(const ra_event_t *event, const char *key, int default_value) {
    if (event == NULL || event->payload_json == NULL) {
        return default_value;
    }
    return (int)extract_json_int64(event->payload_json, key, default_value);
}
