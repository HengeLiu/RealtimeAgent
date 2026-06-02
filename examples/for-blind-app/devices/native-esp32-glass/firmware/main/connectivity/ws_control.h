#ifndef WS_CONTROL_H
#define WS_CONTROL_H

#include <stdbool.h>
#include "esp_err.h"
#include "app/device.h"

// Connection state callback
typedef void (*ws_control_on_connected_cb_t)(void);
typedef void (*ws_control_on_disconnected_cb_t)(void);
typedef void (*ws_control_on_message_cb_t)(const char *event_name, const char *payload, size_t len);

esp_err_t ws_control_init(const char *server_url, const char *device_id,
                          audio_chat_device_t *device);
esp_err_t ws_control_set_callbacks(ws_control_on_connected_cb_t on_connected,
                                    ws_control_on_disconnected_cb_t on_disconnected,
                                    ws_control_on_message_cb_t on_message);
esp_err_t ws_control_send_event(const char *event_name, const char *payload, size_t len);
esp_err_t ws_control_send_event_with_stream(const char *event_name, const char *payload, size_t len,
                                             const char *stream_id, const char *stream_type);
esp_err_t ws_control_send_binary(const uint8_t *data, size_t len);
bool ws_control_is_connected(void);
esp_err_t ws_control_disconnect(void);
esp_err_t ws_control_reconnect(void);
esp_err_t ws_control_task_start(void);

#endif // WS_CONTROL_H