#ifndef WIFI_PROV_H
#define WIFI_PROV_H

#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

typedef enum {
    WIFI_PROV_IDLE,
    WIFI_PROV_AP_ACTIVE,
    WIFI_PROV_CRED_RECEIVED,
    WIFI_PROV_ERROR,
} wifi_prov_state_t;

// Callback when credentials received from captive portal
typedef void (*wifi_prov_cred_cb_t)(const char *ssid, const char *pass,
                                    const char *server_host, uint16_t server_port);

// Start WiFi AP and HTTP server with captive portal
// device_name: AP SSID (e.g. "Glass-XXXX")
esp_err_t wifi_prov_start(const char *device_name);

// Stop AP and HTTP server
esp_err_t wifi_prov_stop(void);

// Get current state
wifi_prov_state_t wifi_prov_get_state(void);

// Set callback for when credentials are received
void wifi_prov_set_cred_callback(wifi_prov_cred_cb_t cb);

#endif // WIFI_PROV_H
