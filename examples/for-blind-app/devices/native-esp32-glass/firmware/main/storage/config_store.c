#include "config_store.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_log.h"
#include "esp_mac.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "config_store";
static const char *NVS_NAMESPACE = "glass_cfg";

#define NVS_KEY_WIFI_SSID   "wifi_ssid"
#define NVS_KEY_WIFI_PASS   "wifi_pass"
#define NVS_KEY_DEVICE_ID   "device_id"
#define NVS_KEY_USER_ID     "user_id"
#define NVS_KEY_HW_ID       "hw_id"
#define NVS_KEY_AUTH_TOKEN  "auth_token"
#define NVS_KEY_SERVER_HOST "server_host"
#define NVS_KEY_SERVER_PORT "server_port"
#define NVS_KEY_CONFIGURED  "configured"
#define NVS_KEY_FAIL_COUNT  "fail_count"

esp_err_t config_store_init(void) {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS partition issue, erasing...");
        nvs_flash_erase();
        ret = nvs_flash_init();
    }
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "NVS init OK");
    } else {
        ESP_LOGE(TAG, "NVS init failed: %d", ret);
    }
    return ret;
}

static esp_err_t read_str(nvs_handle_t h, const char *key, char *out, size_t max_len) {
    size_t len = max_len;
    esp_err_t ret = nvs_get_str(h, key, out, &len);
    if (ret == ESP_ERR_NVS_NOT_FOUND) {
        out[0] = '\0';
        return ESP_OK;
    }
    return ret;
}

esp_err_t config_store_load(device_config_t *cfg) {
    memset(cfg, 0, sizeof(*cfg));

    nvs_handle_t h;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READONLY, &h);
    if (ret != ESP_OK) {
        ESP_LOGI(TAG, "No saved config (nvs_open failed: %d)", ret);
        return ret;
    }

    read_str(h, NVS_KEY_WIFI_SSID, cfg->wifi_ssid, sizeof(cfg->wifi_ssid));
    read_str(h, NVS_KEY_WIFI_PASS, cfg->wifi_pass, sizeof(cfg->wifi_pass));
    read_str(h, NVS_KEY_DEVICE_ID, cfg->device_id, sizeof(cfg->device_id));
    read_str(h, NVS_KEY_USER_ID, cfg->user_id, sizeof(cfg->user_id));
    read_str(h, NVS_KEY_HW_ID, cfg->hw_id, sizeof(cfg->hw_id));
    read_str(h, NVS_KEY_AUTH_TOKEN, cfg->auth_token, sizeof(cfg->auth_token));
    read_str(h, NVS_KEY_SERVER_HOST, cfg->server_host, sizeof(cfg->server_host));

    uint8_t configured = 0;
    nvs_get_u8(h, NVS_KEY_CONFIGURED, &configured);
    cfg->configured = (configured != 0);

    int32_t fail_count = 0;
    nvs_get_i32(h, NVS_KEY_FAIL_COUNT, &fail_count);
    cfg->wifi_fail_count = (int)fail_count;

    int32_t port = 8766;
    nvs_get_i32(h, NVS_KEY_SERVER_PORT, &port);
    cfg->server_port = (uint16_t)port;

    nvs_close(h);

    ESP_LOGI(TAG, "Config loaded: configured=%d ssid=%s device=%s user=%s fail=%d",
             cfg->configured, cfg->wifi_ssid, cfg->device_id, cfg->user_id, cfg->wifi_fail_count);
    return ESP_OK;
}

esp_err_t config_store_save(const device_config_t *cfg) {
    nvs_handle_t h;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "NVS open for write failed: %d", ret);
        return ret;
    }

    nvs_set_str(h, NVS_KEY_WIFI_SSID, cfg->wifi_ssid);
    nvs_set_str(h, NVS_KEY_WIFI_PASS, cfg->wifi_pass);
    nvs_set_str(h, NVS_KEY_DEVICE_ID, cfg->device_id);
    nvs_set_str(h, NVS_KEY_USER_ID, cfg->user_id);
    nvs_set_str(h, NVS_KEY_HW_ID, cfg->hw_id);
    nvs_set_str(h, NVS_KEY_AUTH_TOKEN, cfg->auth_token);
    nvs_set_str(h, NVS_KEY_SERVER_HOST, cfg->server_host);
    nvs_set_i32(h, NVS_KEY_SERVER_PORT, (int32_t)cfg->server_port);
    nvs_set_u8(h, NVS_KEY_CONFIGURED, cfg->configured ? 1 : 0);
    nvs_set_i32(h, NVS_KEY_FAIL_COUNT, (int32_t)cfg->wifi_fail_count);

    ret = nvs_commit(h);
    nvs_close(h);

    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Config saved: ssid=%s device=%s user=%s",
                 cfg->wifi_ssid, cfg->device_id, cfg->user_id);
    } else {
        ESP_LOGE(TAG, "NVS commit failed: %d", ret);
    }
    return ret;
}

esp_err_t config_store_clear_all(void) {
    nvs_handle_t h;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (ret != ESP_OK) {
        return ret;
    }
    nvs_erase_all(h);
    nvs_commit(h);
    nvs_close(h);
    ESP_LOGW(TAG, "All config erased (factory reset)");
    return ESP_OK;
}

esp_err_t config_store_increment_fail_count(int *out_count) {
    nvs_handle_t h;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (ret != ESP_OK) return ret;

    int32_t count = 0;
    nvs_get_i32(h, NVS_KEY_FAIL_COUNT, &count);
    count++;
    nvs_set_i32(h, NVS_KEY_FAIL_COUNT, count);
    nvs_commit(h);
    nvs_close(h);

    if (out_count) *out_count = (int)count;
    ESP_LOGW(TAG, "WiFi fail count: %d", (int)count);
    return ESP_OK;
}

esp_err_t config_store_reset_fail_count(void) {
    nvs_handle_t h;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h);
    if (ret != ESP_OK) return ret;

    nvs_set_i32(h, NVS_KEY_FAIL_COUNT, 0);
    nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "WiFi fail count reset");
    return ESP_OK;
}

void config_store_generate_hw_id(char *out, size_t out_size) {
    uint8_t mac[6];
    esp_efuse_mac_get_default(mac);
    snprintf(out, out_size, "hw-%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

void config_store_generate_device_id(char *out, size_t out_size) {
    uint8_t mac[6];
    esp_efuse_mac_get_default(mac);
    snprintf(out, out_size, "dev-glass-%02x%02x%02x%02x",
             mac[2], mac[3], mac[4], mac[5]);
}
