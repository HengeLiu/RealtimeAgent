#ifndef WS_STREAM_H
#define WS_STREAM_H

#include <stdbool.h>
#include "esp_err.h"

esp_err_t ws_stream_init(const char *server_url, const char *device_id, const char *stream_id);
esp_err_t ws_stream_send_audio(const uint8_t *data, size_t len);
esp_err_t ws_stream_send_final(const char *reason);
esp_err_t ws_stream_send_image(const uint8_t *data, size_t len);
esp_err_t ws_stream_send_sensor_data(const char *json_data, size_t len);
esp_err_t ws_stream_send_imu(const char *json_data, size_t len);
bool ws_stream_is_connected(void);
esp_err_t ws_stream_disconnect(void);
const char* ws_stream_get_user_id(void);
const char* ws_stream_get_session_id(void);
void ws_stream_update_session(const char *new_session_id);

#endif // WS_STREAM_H