#include "realtime_agent_device/ra_client.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

typedef struct {
    char name[RA_MAX_EVENT_NAME_LEN];
    ra_custom_command_handler_t handler;
    void *user_data;
} ra_custom_command_entry_t;

struct ra_device_client {
    ra_device_client_config_t config;
    char server_url[RA_MAX_URL_LEN];
    char device_id[RA_MAX_ID_LEN];
    char user_id[RA_MAX_ID_LEN];
    char name[RA_MAX_NAME_LEN];
    char client_type[64];
    char sdk_version[96];
    char properties_json[1024];
    ra_client_connection_state_t connection_state;
    ra_conversation_state_t conversation_state;
    ra_diagnostics_t diagnostics;
    char session_id[RA_MAX_ID_LEN];
    char mic_stream_id[RA_MAX_ID_LEN];
    char output_stream_id[RA_MAX_ID_LEN];
    char output_codec[RA_MAX_CODEC_LEN];
    int mic_seq;
    int output_last_seq;
    int output_finish_last_seq;
    bool output_finish_pending;
    bool output_started;
    ra_speaker_buffer_t speaker_buffer;
    bool speaker_buffer_initialized;
    ra_connection_state_handler_t connection_handler;
    void *connection_handler_user_data;
    ra_custom_command_entry_t custom_commands[16];
    int custom_command_count;
};

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

static bool has_text(const char *value) {
    return value != NULL && value[0] != '\0';
}

static int send_simple_event(ra_device_client_t *client, const char *name, const char *payload);

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

static void set_connection_state(ra_device_client_t *client, ra_client_connection_state_t state) {
    if (client == NULL) {
        return;
    }
    client->connection_state = state;
    copy_text(client->diagnostics.connection_state, sizeof(client->diagnostics.connection_state), ra_client_connection_state_name(state));
    client->diagnostics.registered = state == RA_CLIENT_REGISTERED;
    if (client->connection_handler != NULL) {
        client->connection_handler(state, client->connection_handler_user_data);
    }
}

static void set_conversation_state(ra_device_client_t *client, ra_conversation_state_t state) {
    if (client == NULL) {
        return;
    }
    client->conversation_state = state;
    copy_text(client->diagnostics.conversation_state, sizeof(client->diagnostics.conversation_state), ra_conversation_state_name(state));
}

ra_audio_format_t ra_audio_format_default(void) {
    ra_audio_format_t format;
    format.codec = RA_DEFAULT_AUDIO_CODEC;
    format.sample_rate = RA_DEFAULT_AUDIO_SAMPLE_RATE;
    format.channels = RA_DEFAULT_AUDIO_CHANNELS;
    format.chunk_ms = RA_DEFAULT_AUDIO_CHUNK_MS;
    return format;
}

size_t ra_audio_format_bytes_per_chunk(const ra_audio_format_t *format) {
    ra_audio_format_t fallback = ra_audio_format_default();
    const ra_audio_format_t *actual = format == NULL ? &fallback : format;
    int sample_rate = actual->sample_rate > 0 ? actual->sample_rate : RA_DEFAULT_AUDIO_SAMPLE_RATE;
    int channels = actual->channels > 0 ? actual->channels : RA_DEFAULT_AUDIO_CHANNELS;
    int chunk_ms = actual->chunk_ms > 0 ? actual->chunk_ms : RA_DEFAULT_AUDIO_CHUNK_MS;
    return (size_t)(sample_rate * channels * 2 * chunk_ms / 1000);
}

static ra_audio_format_t normalize_audio_format(const ra_audio_format_t *format) {
    ra_audio_format_t normalized = ra_audio_format_default();
    if (format == NULL) {
        return normalized;
    }
    if (format->codec != NULL && format->codec[0] != '\0') {
        normalized.codec = format->codec;
    }
    if (format->sample_rate > 0) {
        normalized.sample_rate = format->sample_rate;
    }
    if (format->channels > 0) {
        normalized.channels = format->channels;
    }
    if (format->chunk_ms > 0) {
        normalized.chunk_ms = format->chunk_ms;
    }
    return normalized;
}

static ra_audio_format_t output_format_from_event(const ra_event_t *event, char *codec, size_t codec_capacity) {
    ra_audio_format_t format = ra_audio_format_default();
    if (event == NULL) {
        return format;
    }
    if (codec != NULL && codec_capacity > 0) {
        if (ra_event_extract_payload_string(event, "codec", codec, codec_capacity) == RA_OK && codec[0] != '\0') {
            format.codec = codec;
        }
    }
    format.sample_rate = ra_event_extract_payload_int(event, "sample_rate", format.sample_rate);
    format.channels = ra_event_extract_payload_int(event, "channels", format.channels);
    format.chunk_ms = ra_event_extract_payload_int(event, "chunk_ms", format.chunk_ms);
    return normalize_audio_format(&format);
}

const char *ra_transport_channel_name(ra_transport_channel_t channel) {
    switch (channel) {
        case RA_TRANSPORT_CONTROL:
            return "control";
        case RA_TRANSPORT_AUDIO_INPUT:
            return "audio_input";
        case RA_TRANSPORT_AUDIO_OUTPUT:
            return "audio_output";
        case RA_TRANSPORT_VISUAL_INPUT:
            return "visual_input";
        default:
            return "unknown";
    }
}

const char *ra_client_connection_state_name(ra_client_connection_state_t state) {
    switch (state) {
        case RA_CLIENT_IDLE:
            return "idle";
        case RA_CLIENT_CONNECTING:
            return "connecting";
        case RA_CLIENT_REGISTERING:
            return "registering";
        case RA_CLIENT_REGISTERED:
            return "registered";
        case RA_CLIENT_DISCONNECTED:
            return "disconnected";
        case RA_CLIENT_CLOSED:
            return "closed";
        default:
            return "unknown";
    }
}

const char *ra_conversation_state_name(ra_conversation_state_t state) {
    switch (state) {
        case RA_CONVERSATION_WAITING:
            return "waiting";
        case RA_CONVERSATION_STARTING:
            return "starting";
        case RA_CONVERSATION_ACTIVE:
            return "active";
        case RA_CONVERSATION_CLOSING:
            return "closing";
        default:
            return "unknown";
    }
}

ra_device_client_t *ra_device_client_create(const ra_device_client_config_t *config) {
    if (config == NULL || !has_text(config->server_url) || !has_text(config->device_id) || !has_text(config->user_id)) {
        return NULL;
    }
    ra_device_client_t *client = (ra_device_client_t *)calloc(1, sizeof(*client));
    if (client == NULL) {
        return NULL;
    }
    client->config = *config;
    copy_text(client->server_url, sizeof(client->server_url), config->server_url);
    copy_text(client->device_id, sizeof(client->device_id), config->device_id);
    copy_text(client->user_id, sizeof(client->user_id), config->user_id);
    copy_text(client->name, sizeof(client->name), has_text(config->name) ? config->name : config->device_id);
    copy_text(client->client_type, sizeof(client->client_type), has_text(config->client_type) ? config->client_type : "c-device");
    copy_text(client->sdk_version, sizeof(client->sdk_version), has_text(config->sdk_version) ? config->sdk_version : "realtime-agent-c-device-sdk-0.1.0");
    copy_text(client->properties_json, sizeof(client->properties_json), has_text(config->properties_json) ? config->properties_json : "{}");
    ra_diagnostics_init(&client->diagnostics);
    ra_speaker_buffer_config_t speaker_buffer_config = config->speaker_buffer;
    if (speaker_buffer_config.start_watermark_ms <= 0 &&
        speaker_buffer_config.low_watermark_ms <= 0 &&
        speaker_buffer_config.high_watermark_ms <= 0 &&
        speaker_buffer_config.max_buffer_ms <= 0 &&
        speaker_buffer_config.max_chunks <= 0 &&
        speaker_buffer_config.max_payload_bytes == 0) {
        speaker_buffer_config = ra_speaker_buffer_default_config();
    }
    if (ra_speaker_buffer_init(&client->speaker_buffer, &speaker_buffer_config) == RA_OK) {
        client->speaker_buffer_initialized = true;
        client->config.speaker_buffer = client->speaker_buffer.config;
    }
    set_connection_state(client, RA_CLIENT_IDLE);
    set_conversation_state(client, RA_CONVERSATION_WAITING);
    ra_log_set_level(config->log_level);
    return client;
}

void ra_device_client_destroy(ra_device_client_t *client) {
    if (client == NULL) {
        return;
    }
    if (client->speaker_buffer_initialized) {
        ra_speaker_buffer_deinit(&client->speaker_buffer);
    }
    free(client);
}

int ra_device_client_build_channel_url(const ra_device_client_t *client, ra_transport_channel_t channel, char *out, size_t capacity) {
    if (client == NULL || out == NULL || capacity == 0) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    const char *path = "/ws/control";
    switch (channel) {
        case RA_TRANSPORT_CONTROL:
            path = "/ws/control";
            break;
        case RA_TRANSPORT_AUDIO_INPUT:
            path = "/ws/stream/audio/input";
            break;
        case RA_TRANSPORT_AUDIO_OUTPUT:
            path = "/ws/stream/audio/output";
            break;
        case RA_TRANSPORT_VISUAL_INPUT:
            path = "/ws/stream/visual/input";
            break;
        default:
            return RA_ERROR_INVALID_ARGUMENT;
    }

    char base[RA_MAX_URL_LEN];
    copy_text(base, sizeof(base), client->server_url);
    if (strncmp(base, "http://", 7) == 0) {
        memmove(base + 5, base + 7, strlen(base + 7) + 1);
        memcpy(base, "ws://", 5);
    } else if (strncmp(base, "https://", 8) == 0) {
        memmove(base + 6, base + 8, strlen(base + 8) + 1);
        memcpy(base, "wss://", 6);
    }
    size_t len = strlen(base);
    while (len > 0 && base[len - 1] == '/') {
        base[--len] = '\0';
    }
    int n;
    if (channel == RA_TRANSPORT_CONTROL) {
        n = snprintf(out, capacity, "%s%s", base, path);
    } else {
        n = snprintf(out, capacity, "%s%s?device_id=%s", base, path, client->device_id);
    }
    if (n < 0 || (size_t)n >= capacity) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    return RA_OK;
}

int ra_device_client_build_registration_payload(const ra_device_client_t *client, char *out, size_t capacity, size_t *written) {
    if (client == NULL || out == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    size_t pos = 0;
    int rc = append_text(
        out,
        capacity,
        &pos,
        "{\"device_id\":\"%s\",\"name\":\"%s\",\"client_type\":\"%s\",\"sdk_version\":\"%s\","
        "\"runtime\":{\"language\":\"c\",\"platform\":\"embedded\"},\"properties\":{",
        client->device_id,
        client->name,
        client->client_type,
        client->sdk_version
    );
    if (rc != RA_OK) {
        return rc;
    }
    bool need_comma = false;
    if (client->config.mic != NULL) {
        ra_audio_format_t fmt = normalize_audio_format(&client->config.mic->format);
        rc = append_text(
            out,
            capacity,
            &pos,
            "\"realtime_agent.audio_input\":\"sensor.mic\",\"realtime_agent.audio_input.format\":{\"codec\":\"%s\",\"sample_rate\":%d,\"channels\":%d,\"chunk_ms\":%d}",
            fmt.codec,
            fmt.sample_rate,
            fmt.channels,
            fmt.chunk_ms
        );
        if (rc != RA_OK) {
            return rc;
        }
        need_comma = true;
    }
    if (client->config.speaker != NULL) {
        rc = append_text(
            out,
            capacity,
            &pos,
            "%s\"realtime_agent.audio_output\":\"actuator.speaker\",\"realtime_agent.audio_output.buffer\":{\"start_watermark_ms\":%d,\"low_watermark_ms\":%d,\"high_watermark_ms\":%d,\"max_buffer_ms\":%d}",
            need_comma ? "," : "",
            client->config.speaker_buffer.start_watermark_ms > 0 ? client->config.speaker_buffer.start_watermark_ms : 120,
            client->config.speaker_buffer.low_watermark_ms > 0 ? client->config.speaker_buffer.low_watermark_ms : 300,
            client->config.speaker_buffer.high_watermark_ms > 0 ? client->config.speaker_buffer.high_watermark_ms : 800,
            client->config.speaker_buffer.max_buffer_ms > 0 ? client->config.speaker_buffer.max_buffer_ms : 1200
        );
        if (rc != RA_OK) {
            return rc;
        }
        need_comma = true;
    }
    if (client->custom_command_count > 0) {
        rc = append_text(out, capacity, &pos, "%s\"realtime_agent.custom_commands\":[", need_comma ? "," : "");
        if (rc != RA_OK) {
            return rc;
        }
        for (int i = 0; i < client->custom_command_count; ++i) {
            rc = append_text(out, capacity, &pos, "%s\"%s\"", i == 0 ? "" : ",", client->custom_commands[i].name);
            if (rc != RA_OK) {
                return rc;
            }
        }
        rc = append_text(out, capacity, &pos, "]");
        if (rc != RA_OK) {
            return rc;
        }
    }
    rc = append_text(out, capacity, &pos, "},\"supports\":{\"sensors\":[");
    if (rc != RA_OK) {
        return rc;
    }
    if (client->config.camera != NULL) {
        rc = append_text(out, capacity, &pos, "{\"type\":\"rgb\",\"modes\":[\"single\"],\"default\":{\"format\":\"jpeg\",\"sample_count\":1}}");
        if (rc != RA_OK) {
            return rc;
        }
    }
    rc = append_text(out, capacity, &pos, "],\"actuators\":[]}}");
    if (rc != RA_OK) {
        return rc;
    }
    if (written != NULL) {
        *written = pos;
    }
    return RA_OK;
}

int ra_device_client_register_custom_command(ra_device_client_t *client, const char *name, ra_custom_command_handler_t handler, void *user_data) {
    if (client == NULL || !has_text(name) || handler == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    if (client->custom_command_count >= (int)(sizeof(client->custom_commands) / sizeof(client->custom_commands[0]))) {
        return RA_ERROR_BUFFER_TOO_SMALL;
    }
    ra_custom_command_entry_t *entry = &client->custom_commands[client->custom_command_count++];
    copy_text(entry->name, sizeof(entry->name), name);
    entry->handler = handler;
    entry->user_data = user_data;
    return RA_OK;
}

void ra_device_client_on_connection_state_change(ra_device_client_t *client, ra_connection_state_handler_t handler, void *user_data) {
    if (client == NULL) {
        return;
    }
    client->connection_handler = handler;
    client->connection_handler_user_data = user_data;
}

int ra_device_client_start(ra_device_client_t *client) {
    if (client == NULL || client->config.transport == NULL || client->config.transport->connect == NULL || client->config.transport->send_text == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    set_connection_state(client, RA_CLIENT_CONNECTING);
    char url[RA_MAX_URL_LEN];
    int rc = ra_device_client_build_channel_url(client, RA_TRANSPORT_CONTROL, url, sizeof(url));
    if (rc != RA_OK) {
        return rc;
    }
    rc = client->config.transport->connect(client->config.transport->ctx, RA_TRANSPORT_CONTROL, url);
    if (rc != RA_OK) {
        set_connection_state(client, RA_CLIENT_DISCONNECTED);
        ra_diagnostics_set_error(&client->diagnostics, "control_connect_failed");
        return RA_ERROR_TRANSPORT;
    }
    set_connection_state(client, RA_CLIENT_REGISTERING);
    char payload[2048];
    size_t payload_size = 0;
    rc = ra_device_client_build_registration_payload(client, payload, sizeof(payload), &payload_size);
    if (rc != RA_OK) {
        return rc;
    }
    ra_event_t event;
    ra_event_init(&event, "control.device.register.requested", client->user_id, client->device_id, payload);
    char json[3072];
    size_t json_size = 0;
    rc = ra_event_encode_json(&event, json, sizeof(json), &json_size);
    if (rc != RA_OK) {
        return rc;
    }
    rc = client->config.transport->send_text(client->config.transport->ctx, RA_TRANSPORT_CONTROL, json, json_size);
    if (rc != RA_OK) {
        set_connection_state(client, RA_CLIENT_DISCONNECTED);
        ra_diagnostics_set_error(&client->diagnostics, "register_send_failed");
        return RA_ERROR_TRANSPORT;
    }
    client->diagnostics.sent_events++;
    copy_text(client->diagnostics.last_event_name, sizeof(client->diagnostics.last_event_name), event.event_name);
    return RA_OK;
}

int ra_device_client_close(ra_device_client_t *client) {
    if (client == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    if (client->config.transport != NULL && client->config.transport->close != NULL) {
        (void)client->config.transport->close(client->config.transport->ctx, RA_TRANSPORT_CONTROL);
        (void)client->config.transport->close(client->config.transport->ctx, RA_TRANSPORT_AUDIO_INPUT);
        (void)client->config.transport->close(client->config.transport->ctx, RA_TRANSPORT_AUDIO_OUTPUT);
        (void)client->config.transport->close(client->config.transport->ctx, RA_TRANSPORT_VISUAL_INPUT);
    }
    if (client->config.mic != NULL && client->config.mic->stop != NULL) {
        (void)client->config.mic->stop(client->config.mic->ctx);
    }
    if (client->config.speaker != NULL && client->config.speaker->cancel != NULL) {
        (void)client->config.speaker->cancel(client->config.speaker->ctx);
    }
    set_conversation_state(client, RA_CONVERSATION_WAITING);
    set_connection_state(client, RA_CLIENT_CLOSED);
    return RA_OK;
}

int ra_device_client_start_conversation(ra_device_client_t *client, const char *reason) {
    if (client == NULL || client->config.transport == NULL || client->config.transport->send_text == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    set_conversation_state(client, RA_CONVERSATION_STARTING);
    char payload[256];
    snprintf(payload, sizeof(payload), "{\"reason\":\"%s\",\"audio_input\":%s,\"speaker\":%s,\"camera\":%s}",
             has_text(reason) ? reason : "app_requested",
             client->config.mic != NULL ? "true" : "false",
             client->config.speaker != NULL ? "true" : "false",
             client->config.camera != NULL ? "true" : "false");
    ra_event_t event;
    ra_event_init(&event, "control.user.wake.detected", client->user_id, client->device_id, payload);
    char json[512];
    size_t json_size = 0;
    int rc = ra_event_encode_json(&event, json, sizeof(json), &json_size);
    if (rc != RA_OK) {
        return rc;
    }
    rc = client->config.transport->send_text(client->config.transport->ctx, RA_TRANSPORT_CONTROL, json, json_size);
    if (rc != RA_OK) {
        return RA_ERROR_TRANSPORT;
    }
    client->diagnostics.sent_events++;
    copy_text(client->diagnostics.last_event_name, sizeof(client->diagnostics.last_event_name), event.event_name);
    return RA_OK;
}

int ra_device_client_send_heartbeat(ra_device_client_t *client) {
    if (client == NULL || client->config.transport == NULL || client->config.transport->send_text == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    if (client->connection_state != RA_CLIENT_REGISTERED) {
        return RA_ERROR_STATE;
    }
    char payload[192];
    snprintf(
        payload,
        sizeof(payload),
        "{\"connection_state\":\"online\",\"client_type\":\"%s\"}",
        has_text(client->client_type) ? client->client_type : "c"
    );
    return send_simple_event(client, "control.device.heartbeat.received", payload);
}

static int send_simple_event(ra_device_client_t *client, const char *name, const char *payload) {
    ra_event_t event;
    ra_event_init(&event, name, client->user_id, client->device_id, payload == NULL ? "{}" : payload);
    if (strncmp(name, "stream.output.", strlen("stream.output.")) == 0) {
        copy_text(event.session_id, sizeof(event.session_id), client->device_id);
        copy_text(event.stream_id, sizeof(event.stream_id), client->output_stream_id);
        copy_text(event.stream_type, sizeof(event.stream_type), "actuator.speaker");
    }
    char json[1024];
    size_t size = 0;
    int rc = ra_event_encode_json(&event, json, sizeof(json), &size);
    if (rc != RA_OK) {
        return rc;
    }
    if (client->config.transport == NULL || client->config.transport->send_text == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    rc = client->config.transport->send_text(client->config.transport->ctx, RA_TRANSPORT_CONTROL, json, size);
    if (rc != RA_OK) {
        return RA_ERROR_TRANSPORT;
    }
    client->diagnostics.sent_events++;
    copy_text(client->diagnostics.last_event_name, sizeof(client->diagnostics.last_event_name), name);
    return RA_OK;
}

static void update_speaker_buffer_diagnostics(ra_device_client_t *client) {
    if (client == NULL || !client->speaker_buffer_initialized) {
        return;
    }
    client->diagnostics.speaker_buffered_ms = (uint32_t)(client->speaker_buffer.buffered_ms < 0 ? 0 : client->speaker_buffer.buffered_ms);
    client->diagnostics.speaker_buffered_bytes = (uint32_t)client->speaker_buffer.buffered_bytes;
}

static int send_downstream_watermark_event(ra_device_client_t *client, const char *name, int watermark_ms, const char *reason) {
    char payload[256];
    const char *watermark_key = strcmp(name, "downstream.pause.requested") == 0 ? "high_watermark_ms" : "low_watermark_ms";
    snprintf(
        payload,
        sizeof(payload),
        "{\"stream_type\":\"actuator.speaker\",\"buffered_ms\":%d,\"%s\":%d,\"reason\":\"%s\"}",
        client->speaker_buffer.buffered_ms,
        watermark_key,
        watermark_ms,
        reason
    );
    ra_event_t event;
    ra_event_init(&event, name, client->user_id, client->device_id, payload);
    copy_text(event.session_id, sizeof(event.session_id), client->device_id);
    copy_text(event.stream_id, sizeof(event.stream_id), client->output_stream_id);
    copy_text(event.stream_type, sizeof(event.stream_type), "actuator.speaker");
    char json[1024];
    size_t size = 0;
    int rc = ra_event_encode_json(&event, json, sizeof(json), &size);
    if (rc != RA_OK) {
        return rc;
    }
    if (client->config.transport == NULL || client->config.transport->send_text == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    rc = client->config.transport->send_text(client->config.transport->ctx, RA_TRANSPORT_CONTROL, json, size);
    if (rc != RA_OK) {
        return RA_ERROR_TRANSPORT;
    }
    client->diagnostics.sent_events++;
    copy_text(client->diagnostics.last_event_name, sizeof(client->diagnostics.last_event_name), name);
    return RA_OK;
}

static int start_output_if_ready(ra_device_client_t *client) {
    if (client == NULL || client->output_started || !client->speaker_buffer_initialized) {
        return RA_OK;
    }
    if (!ra_speaker_buffer_can_start(&client->speaker_buffer) &&
        !(client->output_finish_pending && client->speaker_buffer.chunk_count > 0)) {
        return RA_OK;
    }
    client->output_started = true;
    return send_simple_event(client, "stream.output.started", "{}");
}

static int finish_output_if_ready(ra_device_client_t *client) {
    if (client == NULL || !client->output_finish_pending) {
        return RA_OK;
    }
    if (client->output_finish_last_seq >= 0 && client->output_last_seq < client->output_finish_last_seq) {
        return RA_OK;
    }
    if (client->speaker_buffer_initialized && client->speaker_buffer.chunk_count > 0) {
        return RA_OK;
    }
    client->output_finish_pending = false;
    if (client->config.speaker != NULL && client->config.speaker->drain != NULL) {
        (void)client->config.speaker->drain(client->config.speaker->ctx);
    }
    return send_simple_event(client, "stream.output.finished", "{}");
}

static int connect_channel_if_possible(ra_device_client_t *client, ra_transport_channel_t channel) {
    if (client->config.transport == NULL || client->config.transport->connect == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    char url[RA_MAX_URL_LEN];
    int rc = ra_device_client_build_channel_url(client, channel, url, sizeof(url));
    if (rc != RA_OK) {
        return rc;
    }
    rc = client->config.transport->connect(client->config.transport->ctx, channel, url);
    return rc == 0 ? RA_OK : RA_ERROR_TRANSPORT;
}

static int send_stream_chunk(ra_device_client_t *client, ra_transport_channel_t channel, ra_stream_chunk_t *chunk) {
    if (client->config.transport == NULL || client->config.transport->send_binary == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    const size_t metadata_size = chunk->metadata_json == NULL ? 2 : strlen(chunk->metadata_json);
    const size_t encoded_capacity = chunk->payload_size + metadata_size + 1024;
    uint8_t *encoded = (uint8_t *)malloc(encoded_capacity);
    if (encoded == NULL) {
        return RA_ERROR_NO_MEMORY;
    }
    size_t written = 0;
    int rc = ra_stream_chunk_encode(chunk, encoded, encoded_capacity, &written);
    if (rc != RA_OK) {
        free(encoded);
        return rc;
    }
    rc = client->config.transport->send_binary(client->config.transport->ctx, channel, encoded, written);
    free(encoded);
    if (rc != 0) {
        return RA_ERROR_TRANSPORT;
    }
    client->diagnostics.sent_stream_chunks++;
    return RA_OK;
}

static int send_input_stream_lifecycle_event(
    ra_device_client_t *client,
    const char *name,
    const char *stream_id,
    const char *request_id
) {
    char payload[256];
    if (has_text(request_id)) {
        snprintf(payload, sizeof(payload), "{\"stream_type\":\"sensor.rgb\",\"request_id\":\"%s\"}", request_id);
    } else {
        snprintf(payload, sizeof(payload), "%s", "{\"stream_type\":\"sensor.rgb\"}");
    }
    ra_event_t event;
    ra_event_init(&event, name, client->user_id, client->device_id, payload);
    copy_text(event.session_id, sizeof(event.session_id), client->session_id[0] == '\0' ? client->device_id : client->session_id);
    copy_text(event.stream_id, sizeof(event.stream_id), stream_id);
    copy_text(event.stream_type, sizeof(event.stream_type), "sensor.rgb");
    char json[1024];
    size_t size = 0;
    int rc = ra_event_encode_json(&event, json, sizeof(json), &size);
    if (rc != RA_OK) {
        return rc;
    }
    if (client->config.transport == NULL || client->config.transport->send_text == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    rc = client->config.transport->send_text(client->config.transport->ctx, RA_TRANSPORT_CONTROL, json, size);
    if (rc != RA_OK) {
        return RA_ERROR_TRANSPORT;
    }
    client->diagnostics.sent_events++;
    copy_text(client->diagnostics.last_event_name, sizeof(client->diagnostics.last_event_name), name);
    return RA_OK;
}

static void update_session_from_event(ra_device_client_t *client, const ra_event_t *event) {
    if (event->session_id[0] != '\0') {
        copy_text(client->session_id, sizeof(client->session_id), event->session_id);
    } else {
        char session_id[RA_MAX_ID_LEN];
        if (ra_event_extract_payload_string(event, "session_id", session_id, sizeof(session_id)) == RA_OK) {
            copy_text(client->session_id, sizeof(client->session_id), session_id);
        }
    }
    if (client->session_id[0] == '\0') {
        snprintf(client->session_id, sizeof(client->session_id), "session_%lld", (long long)ra_now_ms());
    }

    char stream_id[RA_MAX_ID_LEN];
    if (ra_event_extract_payload_string(event, "audio_input_stream_id", stream_id, sizeof(stream_id)) == RA_OK ||
        ra_event_extract_payload_string(event, "stream_id", stream_id, sizeof(stream_id)) == RA_OK) {
        copy_text(client->mic_stream_id, sizeof(client->mic_stream_id), stream_id);
    }
    if (client->mic_stream_id[0] == '\0') {
        snprintf(client->mic_stream_id, sizeof(client->mic_stream_id), "mic_%lld", (long long)ra_now_ms());
    }
}

int ra_device_client_send_mic_chunk(ra_device_client_t *client) {
    if (client == NULL || client->config.mic == NULL || client->config.mic->read == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    if (client->conversation_state != RA_CONVERSATION_ACTIVE) {
        return RA_ERROR_STATE;
    }
    const size_t pcm_capacity = 2048;
    uint8_t *pcm = (uint8_t *)malloc(pcm_capacity);
    if (pcm == NULL) {
        return RA_ERROR_NO_MEMORY;
    }
    size_t written = 0;
    int rc = client->config.mic->read(client->config.mic->ctx, pcm, pcm_capacity, &written);
    if (rc != 0) {
        free(pcm);
        return RA_ERROR_HARDWARE;
    }
    ra_audio_format_t format = normalize_audio_format(&client->config.mic->format);
    ra_stream_chunk_t chunk;
    ra_stream_chunk_init(&chunk);
    copy_text(chunk.user_id, sizeof(chunk.user_id), client->user_id);
    copy_text(chunk.session_id, sizeof(chunk.session_id), client->session_id);
    copy_text(chunk.stream_id, sizeof(chunk.stream_id), client->mic_stream_id);
    copy_text(chunk.stream_type, sizeof(chunk.stream_type), "sensor.mic");
    copy_text(chunk.codec, sizeof(chunk.codec), format.codec);
    chunk.sample_rate = format.sample_rate;
    chunk.channels = format.channels;
    chunk.duration_ms = format.chunk_ms;
    chunk.seq = client->mic_seq++;
    chunk.payload = pcm;
    chunk.payload_size = written;
    rc = send_stream_chunk(client, RA_TRANSPORT_AUDIO_INPUT, &chunk);
    free(pcm);
    return rc;
}

int ra_device_client_handle_output_chunk(ra_device_client_t *client, const uint8_t *data, size_t size) {
    if (client == NULL || data == NULL || client->config.speaker == NULL || !client->speaker_buffer_initialized) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    ra_stream_chunk_t chunk;
    const uint8_t *payload = NULL;
    int rc = ra_stream_chunk_decode(data, size, &chunk, &payload);
    if (rc != RA_OK) {
        return rc;
    }
    client->diagnostics.received_output_chunks++;
    if (strcmp(chunk.stream_type, "actuator.speaker") != 0) {
        return RA_ERROR_STATE;
    }
    if (client->output_stream_id[0] == '\0') {
        copy_text(client->output_stream_id, sizeof(client->output_stream_id), chunk.stream_id);
    } else if (strcmp(client->output_stream_id, chunk.stream_id) != 0) {
        return RA_OK;
    }
    rc = ra_speaker_buffer_append(&client->speaker_buffer, chunk.seq, payload, chunk.payload_size, chunk.duration_ms);
    if (rc != RA_OK) {
        return rc;
    }
    update_speaker_buffer_diagnostics(client);
    if (ra_speaker_buffer_should_pause(&client->speaker_buffer)) {
        (void)send_downstream_watermark_event(
            client,
            "downstream.pause.requested",
            client->speaker_buffer.config.high_watermark_ms,
            "speaker_buffer_high"
        );
    }
    return start_output_if_ready(client);
}

int ra_device_client_pump_output(ra_device_client_t *client) {
    if (client == NULL || client->config.speaker == NULL || !client->speaker_buffer_initialized) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    int rc = start_output_if_ready(client);
    if (rc != RA_OK) {
        return rc;
    }
    if (!client->output_started) {
        return RA_ERROR_STATE;
    }
    ra_speaker_buffer_chunk_t out;
    rc = ra_speaker_buffer_pop_next(&client->speaker_buffer, &out);
    if (rc == RA_ERROR_NOT_FOUND) {
        return finish_output_if_ready(client);
    }
    if (rc != RA_OK) {
        return rc;
    }
    if (client->config.speaker->write != NULL) {
        int write_rc = client->config.speaker->write(client->config.speaker->ctx, out.payload, out.size, out.duration_ms);
        if (write_rc != 0) {
            ra_speaker_buffer_release_chunk(&out);
            return RA_ERROR_HARDWARE;
        }
    }
    client->output_last_seq = out.seq;
    ra_speaker_buffer_release_chunk(&out);
    update_speaker_buffer_diagnostics(client);
    if (ra_speaker_buffer_should_resume(&client->speaker_buffer)) {
        (void)send_downstream_watermark_event(
            client,
            "downstream.resume.requested",
            client->speaker_buffer.config.low_watermark_ms,
            "speaker_buffer_low"
        );
    }
    return finish_output_if_ready(client);
}

static int handle_rgb_request(ra_device_client_t *client, const ra_event_t *event) {
    if (client->config.camera == NULL || client->config.camera->capture_jpeg == NULL) {
        return send_simple_event(client, "stream.input.failed", "{\"stream_type\":\"sensor.rgb\",\"reason\":\"camera_unavailable\"}");
    }
    int rc = connect_channel_if_possible(client, RA_TRANSPORT_VISUAL_INPUT);
    if (rc != RA_OK) {
        return rc;
    }
    const uint8_t *jpeg = NULL;
    size_t jpeg_size = 0;
    rc = client->config.camera->capture_jpeg(client->config.camera->ctx, &jpeg, &jpeg_size);
    if (rc != 0 || jpeg == NULL || jpeg_size == 0) {
        return send_simple_event(client, "stream.input.failed", "{\"stream_type\":\"sensor.rgb\",\"reason\":\"capture_failed\"}");
    }
    char stream_id[RA_MAX_ID_LEN];
    if (event->stream_id[0] != '\0') {
        copy_text(stream_id, sizeof(stream_id), event->stream_id);
    } else if (ra_event_extract_payload_string(event, "stream_id", stream_id, sizeof(stream_id)) != RA_OK) {
        snprintf(stream_id, sizeof(stream_id), "rgb_%lld", (long long)ra_now_ms());
    }
    char request_id[RA_MAX_ID_LEN];
    request_id[0] = '\0';
    (void)ra_event_extract_payload_string(event, "request_id", request_id, sizeof(request_id));
    (void)send_input_stream_lifecycle_event(client, "stream.input.opened", stream_id, request_id);
    ra_stream_chunk_t chunk;
    ra_stream_chunk_init(&chunk);
    copy_text(chunk.user_id, sizeof(chunk.user_id), client->user_id);
    copy_text(chunk.session_id, sizeof(chunk.session_id), client->session_id[0] == '\0' ? "default" : client->session_id);
    copy_text(chunk.stream_id, sizeof(chunk.stream_id), stream_id);
    copy_text(chunk.stream_type, sizeof(chunk.stream_type), "sensor.rgb");
    copy_text(chunk.codec, sizeof(chunk.codec), "jpeg");
    char metadata[192];
    if (has_text(request_id)) {
        snprintf(metadata, sizeof(metadata), "{\"request_id\":\"%s\"}", request_id);
    } else {
        snprintf(metadata, sizeof(metadata), "%s", "{}");
    }
    chunk.sample_rate = 0;
    chunk.channels = 0;
    chunk.duration_ms = 0;
    chunk.final = true;
    chunk.metadata_json = metadata;
    chunk.payload = jpeg;
    chunk.payload_size = jpeg_size;
    rc = send_stream_chunk(client, RA_TRANSPORT_VISUAL_INPUT, &chunk);
    if (client->config.camera->release_jpeg != NULL) {
        client->config.camera->release_jpeg(client->config.camera->ctx, jpeg);
    }
    if (rc != RA_OK) {
        return rc;
    }
    return send_input_stream_lifecycle_event(client, "stream.input.closed", stream_id, request_id);
}

int ra_device_client_handle_event(ra_device_client_t *client, const char *json) {
    if (client == NULL || json == NULL) {
        return RA_ERROR_INVALID_ARGUMENT;
    }
    ra_event_t event;
    int rc = ra_event_decode_json(json, &event);
    if (rc != RA_OK) {
        return rc;
    }
    client->diagnostics.received_events++;
    copy_text(client->diagnostics.last_event_name, sizeof(client->diagnostics.last_event_name), event.event_name);
    if (strcmp(event.event_name, "control.device.registered") == 0) {
        set_connection_state(client, RA_CLIENT_REGISTERED);
        return RA_OK;
    }
    if (strcmp(event.event_name, "control.device.register.failed") == 0) {
        set_connection_state(client, RA_CLIENT_IDLE);
        ra_diagnostics_set_error(&client->diagnostics, event.payload_json);
        return RA_ERROR_STATE;
    }
    if (strcmp(event.event_name, "control.audio_session.open.requested") == 0) {
        update_session_from_event(client, &event);
        client->mic_seq = 0;
        (void)connect_channel_if_possible(client, RA_TRANSPORT_AUDIO_INPUT);
        (void)connect_channel_if_possible(client, RA_TRANSPORT_AUDIO_OUTPUT);
        if (client->config.mic != NULL && client->config.mic->start != NULL) {
            rc = client->config.mic->start(client->config.mic->ctx);
            if (rc != RA_OK) {
                return RA_ERROR_HARDWARE;
            }
        }
        set_conversation_state(client, RA_CONVERSATION_ACTIVE);
        char payload[256];
        snprintf(payload, sizeof(payload), "{\"audio_input\":\"sensor.mic\",\"stream_id\":\"%s\",\"session_id\":\"%s\"}", client->mic_stream_id, client->session_id);
        return send_simple_event(client, "control.audio_session.opened", payload);
    }
    if (strcmp(event.event_name, "control.audio_session.close.requested") == 0) {
        if (client->config.mic != NULL && client->config.mic->stop != NULL) {
            (void)client->config.mic->stop(client->config.mic->ctx);
        }
        if (client->config.speaker != NULL && client->config.speaker->cancel != NULL) {
            (void)client->config.speaker->cancel(client->config.speaker->ctx);
        }
        set_conversation_state(client, RA_CONVERSATION_WAITING);
        return send_simple_event(client, "control.audio_session.closed", "{\"reason\":\"server_requested\"}");
    }
    if (strcmp(event.event_name, "stream.output.start.requested") == 0) {
        client->output_started = false;
        client->output_last_seq = -1;
        client->output_finish_last_seq = -1;
        client->output_finish_pending = false;
        if (client->speaker_buffer_initialized) {
            ra_speaker_buffer_reset(&client->speaker_buffer, 0);
            update_speaker_buffer_diagnostics(client);
        }
        if (event.stream_id[0] != '\0') {
            copy_text(client->output_stream_id, sizeof(client->output_stream_id), event.stream_id);
        } else {
            char output_stream_id[RA_MAX_ID_LEN];
            if (ra_event_extract_payload_string(&event, "stream_id", output_stream_id, sizeof(output_stream_id)) == RA_OK) {
                copy_text(client->output_stream_id, sizeof(client->output_stream_id), output_stream_id);
            }
        }
        if (client->config.speaker != NULL && client->config.speaker->prepare != NULL) {
            char codec[32];
            ra_audio_format_t fmt = output_format_from_event(&event, codec, sizeof(codec));
            copy_text(client->output_codec, sizeof(client->output_codec), fmt.codec);
            rc = client->config.speaker->prepare(client->config.speaker->ctx, &fmt);
            if (rc != RA_OK) {
                return RA_ERROR_HARDWARE;
            }
        }
        return send_simple_event(client, "stream.output.ready", "{}");
    }
    if (strcmp(event.event_name, "stream.control.open.requested") == 0 && ra_event_payload_contains(&event, "sensor.rgb")) {
        return handle_rgb_request(client, &event);
    }
    if (strcmp(event.event_name, "stream.output.cancel.requested") == 0) {
        client->output_finish_pending = false;
        client->output_finish_last_seq = -1;
        client->output_started = false;
        if (client->speaker_buffer_initialized) {
            ra_speaker_buffer_reset(&client->speaker_buffer, 0);
            update_speaker_buffer_diagnostics(client);
        }
        if (client->config.speaker != NULL && client->config.speaker->cancel != NULL) {
            (void)client->config.speaker->cancel(client->config.speaker->ctx);
        }
        return send_simple_event(client, "stream.output.cancelled", "{}");
    }
    if (strcmp(event.event_name, "stream.output.finish.requested") == 0) {
        client->output_finish_last_seq = ra_event_extract_payload_int(&event, "output_last_seq", -1);
        client->output_finish_pending = true;
        return finish_output_if_ready(client);
    }
    if (strcmp(event.event_name, "custom.command.requested") == 0) {
        char command[RA_MAX_EVENT_NAME_LEN];
        if (ra_event_extract_payload_string(&event, "command", command, sizeof(command)) == RA_OK) {
            for (int i = 0; i < client->custom_command_count; ++i) {
                if (strcmp(client->custom_commands[i].name, command) == 0) {
                    client->custom_commands[i].handler(&event, client->custom_commands[i].user_data);
                    return RA_OK;
                }
            }
        }
    }
    return RA_OK;
}

ra_client_connection_state_t ra_device_client_connection_state(const ra_device_client_t *client) {
    return client == NULL ? RA_CLIENT_CLOSED : client->connection_state;
}

ra_conversation_state_t ra_device_client_conversation_state(const ra_device_client_t *client) {
    return client == NULL ? RA_CONVERSATION_WAITING : client->conversation_state;
}

void ra_device_client_get_diagnostics(const ra_device_client_t *client, ra_diagnostics_t *out) {
    if (client == NULL || out == NULL) {
        return;
    }
    *out = client->diagnostics;
}
