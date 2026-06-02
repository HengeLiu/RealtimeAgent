#ifndef CONFIG_STORE_H
#define CONFIG_STORE_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    char wifi_ssid[33];
    char wifi_pass[65];
    char device_id[64];
    char user_id[64];
    char hw_id[24];
    char auth_token[256];
    char server_host[64];
    uint16_t server_port;
    bool configured;
    int wifi_fail_count;
} device_config_t;

esp_err_t config_store_init(void);
esp_err_t config_store_load(device_config_t *cfg);
esp_err_t config_store_save(const device_config_t *cfg);
esp_err_t config_store_clear_all(void);
esp_err_t config_store_increment_fail_count(int *out_count);
esp_err_t config_store_reset_fail_count(void);

void config_store_generate_hw_id(char *out, size_t out_size);
void config_store_generate_device_id(char *out, size_t out_size);

#ifdef __cplusplus
}
#endif

#endif // CONFIG_STORE_H
