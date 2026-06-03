#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

#include <stdbool.h>
#include "esp_err.h"

typedef enum {
    WIFI_STATE_IDLE,
    WIFI_STATE_CONNECTING,
    WIFI_STATE_CONNECTED,
    WIFI_STATE_FAILED
} wifi_state_t;

typedef void (*wifi_on_connected_cb_t)(void);
typedef void (*wifi_on_disconnected_cb_t)(void);

esp_err_t wifi_manager_init(const char *ssid, const char *password);
esp_err_t wifi_manager_set_callbacks(wifi_on_connected_cb_t on_connected,
                                      wifi_on_disconnected_cb_t on_disconnected);
wifi_state_t wifi_manager_get_state(void);
bool wifi_manager_is_connected(void);
const char* wifi_manager_get_local_ip(void);

// AP mode for provisioning
esp_err_t wifi_manager_start_ap(const char *ssid);
esp_err_t wifi_manager_stop_ap(void);
esp_err_t wifi_manager_scan_and_connect(const char *ssid, const char *password);

#endif // WIFI_MANAGER_H
