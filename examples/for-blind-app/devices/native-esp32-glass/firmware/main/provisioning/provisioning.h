#ifndef PROVISIONING_H
#define PROVISIONING_H

#include "esp_err.h"
#include "storage/config_store.h"

typedef enum {
    PROV_STATE_IDLE,
    PROV_STATE_AP_STARTED,
    PROV_STATE_WAITING_CREDENTIALS,
    PROV_STATE_CONNECTING_WIFI,
    PROV_STATE_PAIRING,
    PROV_STATE_DONE,
    PROV_STATE_ERROR,
} provisioning_state_t;

esp_err_t provisioning_start(const device_config_t *config);
esp_err_t provisioning_stop(void);
provisioning_state_t provisioning_get_state(void);

#endif // PROVISIONING_H
