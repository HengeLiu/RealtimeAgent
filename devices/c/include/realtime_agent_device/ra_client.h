#ifndef REALTIME_AGENT_DEVICE_RA_CLIENT_H
#define REALTIME_AGENT_DEVICE_RA_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "ra_diagnostics.h"
#include "ra_event.h"
#include "ra_hardware.h"
#include "ra_log.h"
#include "ra_speaker_buffer.h"
#include "ra_stream_chunk.h"
#include "ra_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    RA_CLIENT_IDLE = 0,
    RA_CLIENT_CONNECTING,
    RA_CLIENT_REGISTERING,
    RA_CLIENT_REGISTERED,
    RA_CLIENT_DISCONNECTED,
    RA_CLIENT_CLOSED,
} ra_client_connection_state_t;

typedef enum {
    RA_CONVERSATION_WAITING = 0,
    RA_CONVERSATION_STARTING,
    RA_CONVERSATION_ACTIVE,
    RA_CONVERSATION_CLOSING,
} ra_conversation_state_t;

typedef struct ra_device_client ra_device_client_t;
typedef void (*ra_connection_state_handler_t)(ra_client_connection_state_t state, void *user_data);
typedef void (*ra_custom_command_handler_t)(const ra_event_t *event, void *user_data);

typedef struct {
    const char *server_url;
    const char *device_id;
    const char *user_id;
    const char *name;
    const char *client_type;
    const char *sdk_version;
    const char *properties_json;
    ra_mic_source_t *mic;
    ra_camera_source_t *camera;
    ra_speaker_sink_t *speaker;
    ra_transport_t *transport;
    ra_speaker_buffer_config_t speaker_buffer;
    ra_log_level_t log_level;
} ra_device_client_config_t;

ra_device_client_t *ra_device_client_create(const ra_device_client_config_t *config);
void ra_device_client_destroy(ra_device_client_t *client);
int ra_device_client_start(ra_device_client_t *client);
int ra_device_client_close(ra_device_client_t *client);
int ra_device_client_send_heartbeat(ra_device_client_t *client);
int ra_device_client_start_conversation(ra_device_client_t *client, const char *reason);
int ra_device_client_handle_event(ra_device_client_t *client, const char *json);
int ra_device_client_send_mic_chunk(ra_device_client_t *client);
int ra_device_client_handle_output_chunk(ra_device_client_t *client, const uint8_t *data, size_t size);
int ra_device_client_pump_output(ra_device_client_t *client);
int ra_device_client_build_registration_payload(const ra_device_client_t *client, char *out, size_t capacity, size_t *written);
int ra_device_client_build_channel_url(const ra_device_client_t *client, ra_transport_channel_t channel, char *out, size_t capacity);
int ra_device_client_register_custom_command(ra_device_client_t *client, const char *name, ra_custom_command_handler_t handler, void *user_data);
void ra_device_client_on_connection_state_change(ra_device_client_t *client, ra_connection_state_handler_t handler, void *user_data);
ra_client_connection_state_t ra_device_client_connection_state(const ra_device_client_t *client);
ra_conversation_state_t ra_device_client_conversation_state(const ra_device_client_t *client);
void ra_device_client_get_diagnostics(const ra_device_client_t *client, ra_diagnostics_t *out);
const char *ra_client_connection_state_name(ra_client_connection_state_t state);
const char *ra_conversation_state_name(ra_conversation_state_t state);

#ifdef __cplusplus
}
#endif

#endif
