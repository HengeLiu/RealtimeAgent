#ifndef BLE_PROV_H
#define BLE_PROV_H

#include "esp_err.h"
#include <stdbool.h>

typedef enum {
    BLE_PROV_IDLE,
    BLE_PROV_ADVERTISING,
    BLE_PROV_CONNECTED,
    BLE_PROV_CRED_RECEIVED,
    BLE_PROV_DONE,
    BLE_PROV_ERROR,
} ble_prov_state_t;

// Callback when all credentials received from App
typedef void (*ble_prov_cred_cb_t)(const char *ssid, const char *pass,
                                    const char *pairing_code,
                                    const char *server_host, uint16_t server_port);

// Start BLE advertising and GATT server with given device name (e.g. "Glass-XXXX")
esp_err_t ble_prov_start(const char *device_name);

// Stop BLE and release all resources
esp_err_t ble_prov_stop(void);

// Get current provisioning BLE state
ble_prov_state_t ble_prov_get_state(void);

// Set callback for when credentials are received
void ble_prov_set_cred_callback(ble_prov_cred_cb_t cb);

// Send status notification to connected App ("connecting", "wifi_ok", "pair_ok", "fail:reason")
void ble_prov_send_status(const char *status);

#endif // BLE_PROV_H
