#include "provisioning.h"
#include "ble_prov.h"
#include "storage/config_store.h"
#include "connectivity/wifi_manager.h"
#include "esp_log.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "provisioning";

static provisioning_state_t s_state = PROV_STATE_IDLE;
static device_config_t s_config;
static TaskHandle_t s_prov_task = NULL;

// Credentials received from BLE (set in callback, consumed in task)
static volatile bool s_cred_received = false;
static char s_cred_ssid[33];
static char s_cred_pass[65];
static char s_cred_code[8];
static char s_cred_server[64];
static uint16_t s_cred_port = 8766;

static char s_ap_ssid[32];

// Forward declarations
static void prov_task(void *pvParameters);
static void on_ble_credentials(const char *ssid, const char *pass,
                                const char *pairing_code,
                                const char *server_host, uint16_t server_port);
static esp_err_t call_pairing_api(const char *server_host, uint16_t server_port,
                                   const char *pairing_code, const char *hw_id,
                                   char *out_user_id, size_t user_id_size,
                                   char *out_device_id, size_t device_id_size,
                                   char *out_token, size_t token_size);

esp_err_t provisioning_start(const device_config_t *config) {
    if (s_state != PROV_STATE_IDLE && s_state != PROV_STATE_ERROR) {
        ESP_LOGW(TAG, "Provisioning already in progress (state=%d)", s_state);
        return ESP_OK;
    }

    memcpy(&s_config, config, sizeof(s_config));

    // Generate BLE device name from hardware ID
    snprintf(s_ap_ssid, sizeof(s_ap_ssid), "Glass-%s", config->hw_id + 3); // skip "hw-"

    s_cred_received = false;
    s_state = PROV_STATE_WAITING_CREDENTIALS;

    xTaskCreatePinnedToCore(&prov_task, "prov_task", 8192, NULL, 5, &s_prov_task, 0);

    ESP_LOGI(TAG, "BLE provisioning started, advertising as: %s", s_ap_ssid);
    return ESP_OK;
}

esp_err_t provisioning_stop(void) {
    ble_prov_stop();
    s_state = PROV_STATE_IDLE;
    if (s_prov_task) {
        vTaskDelete(s_prov_task);
        s_prov_task = NULL;
    }
    return ESP_OK;
}

provisioning_state_t provisioning_get_state(void) {
    return s_state;
}

static void on_ble_credentials(const char *ssid, const char *pass,
                                const char *pairing_code,
                                const char *server_host, uint16_t server_port) {
    // Store credentials for the task to consume
    strncpy(s_cred_ssid, ssid, sizeof(s_cred_ssid) - 1);
    strncpy(s_cred_pass, pass, sizeof(s_cred_pass) - 1);
    strncpy(s_cred_code, pairing_code, sizeof(s_cred_code) - 1);
    strncpy(s_cred_server, server_host, sizeof(s_cred_server) - 1);
    s_cred_port = server_port;
    s_cred_received = true;

    ESP_LOGI(TAG, "BLE credentials received: ssid=%s code=%s server=%s:%d",
             s_cred_ssid, s_cred_code, s_cred_server, s_cred_port);
}

static void prov_task(void *pvParameters) {
    (void)pvParameters;

    // Step 1: Start BLE advertising
    ble_prov_set_cred_callback(on_ble_credentials);
    esp_err_t ret = ble_prov_start(s_ap_ssid);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start BLE: %d", ret);
        s_state = PROV_STATE_ERROR;
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Waiting for BLE credentials...");

    // Wait for credentials from BLE
    while (!s_cred_received) {
        vTaskDelay(pdMS_TO_TICKS(200));
    }

    // Step 2: Connect to WiFi
    ble_prov_send_status("connecting");
    s_state = PROV_STATE_CONNECTING_WIFI;
    ESP_LOGI(TAG, "Connecting to WiFi: %s", s_cred_ssid);

    ret = wifi_manager_scan_and_connect(s_cred_ssid, s_cred_pass);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WiFi connection failed");
        ble_prov_send_status("fail:wifi");
        s_state = PROV_STATE_ERROR;
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "WiFi connected, IP: %s", wifi_manager_get_local_ip());
    ble_prov_send_status("wifi_ok");

    // Step 3: Call pairing API
    s_state = PROV_STATE_PAIRING;
    ESP_LOGI(TAG, "Calling pairing API...");

    char user_id[64] = {0};
    char device_id[64] = {0};
    char auth_token[256] = {0};

    // Use server from BLE if provided, otherwise use default
    const char *server_host = s_cred_server[0] ? s_cred_server : s_config.server_host;
    uint16_t server_port = s_cred_port ? s_cred_port : s_config.server_port;

    ret = call_pairing_api(
        server_host, server_port,
        s_cred_code, s_config.hw_id,
        user_id, sizeof(user_id),
        device_id, sizeof(device_id),
        auth_token, sizeof(auth_token)
    );

    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Pairing API failed: %d", ret);
        ble_prov_send_status("fail:pair");
        s_state = PROV_STATE_ERROR;
        vTaskDelete(NULL);
        return;
    }

    // Step 4: Save to NVS
    ESP_LOGI(TAG, "Pairing successful! user=%s device=%s", user_id, device_id);

    strncpy(s_config.wifi_ssid, s_cred_ssid, sizeof(s_config.wifi_ssid) - 1);
    strncpy(s_config.wifi_pass, s_cred_pass, sizeof(s_config.wifi_pass) - 1);
    strncpy(s_config.user_id, user_id, sizeof(s_config.user_id) - 1);
    strncpy(s_config.device_id, device_id, sizeof(s_config.device_id) - 1);
    strncpy(s_config.auth_token, auth_token, sizeof(s_config.auth_token) - 1);
    if (server_host[0]) {
        strncpy(s_config.server_host, server_host, sizeof(s_config.server_host) - 1);
        s_config.server_port = server_port;
    }
    s_config.configured = true;
    s_config.wifi_fail_count = 0;

    ret = config_store_save(&s_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to save config: %d", ret);
        ble_prov_send_status("fail:save");
        s_state = PROV_STATE_ERROR;
        vTaskDelete(NULL);
        return;
    }

    // Step 5: Notify success and stop BLE
    ble_prov_send_status("pair_ok");
    vTaskDelay(pdMS_TO_TICKS(500));  // Give App time to receive the notification

    ble_prov_stop();

    s_state = PROV_STATE_DONE;
    ESP_LOGI(TAG, "=== PROVISIONING COMPLETE ===");

    s_prov_task = NULL;
    vTaskDelete(NULL);
}

static esp_err_t call_pairing_api(const char *server_host, uint16_t server_port,
                                   const char *pairing_code, const char *hw_id,
                                   char *out_user_id, size_t user_id_size,
                                   char *out_device_id, size_t device_id_size,
                                   char *out_token, size_t token_size) {
    char url[128];
    snprintf(url, sizeof(url), "http://%s:%d/api/device/pair", server_host, server_port);

    char post_data[256];
    snprintf(post_data, sizeof(post_data),
             "{\"pairing_code\":\"%s\",\"hardware_id\":\"%s\",\"device_name\":\"ESP32 Glass\"}",
             pairing_code, hw_id);

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 10000,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, post_data, strlen(post_data));

    esp_err_t err = esp_http_client_perform(client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP request failed: %d", err);
        esp_http_client_cleanup(client);
        return err;
    }

    int status = esp_http_client_get_status_code(client);
    int content_length = esp_http_client_get_content_length(client);

    if (status != 200) {
        ESP_LOGE(TAG, "Pairing API returned status %d", status);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    // Read response
    char *resp_buf = malloc(content_length + 1);
    if (!resp_buf) {
        esp_http_client_cleanup(client);
        return ESP_ERR_NO_MEM;
    }

    int read_len = esp_http_client_read(client, resp_buf, content_length);
    resp_buf[read_len] = '\0';
    esp_http_client_cleanup(client);

    ESP_LOGI(TAG, "Pairing response: %s", resp_buf);

    // Parse JSON response (simple parser, no cJSON)
    char *p;

    p = strstr(resp_buf, "\"user_id\"");
    if (p) {
        p = strchr(p, ':');
        if (p) {
            p = strchr(p, '"');
            if (p) {
                p++;
                char *end = strchr(p, '"');
                if (end) {
                    size_t len = end - p;
                    if (len >= user_id_size) len = user_id_size - 1;
                    strncpy(out_user_id, p, len);
                }
            }
        }
    }

    p = strstr(resp_buf, "\"device_id\"");
    if (p) {
        p = strchr(p, ':');
        if (p) {
            p = strchr(p, '"');
            if (p) {
                p++;
                char *end = strchr(p, '"');
                if (end) {
                    size_t len = end - p;
                    if (len >= device_id_size) len = device_id_size - 1;
                    strncpy(out_device_id, p, len);
                }
            }
        }
    }

    p = strstr(resp_buf, "\"auth_token\"");
    if (p) {
        p = strchr(p, ':');
        if (p) {
            p = strchr(p, '"');
            if (p) {
                p++;
                char *end = strchr(p, '"');
                if (end) {
                    size_t len = end - p;
                    if (len >= token_size) len = token_size - 1;
                    strncpy(out_token, p, len);
                }
            }
        }
    }

    free(resp_buf);

    if (out_user_id[0] == '\0' || out_device_id[0] == '\0') {
        ESP_LOGE(TAG, "Failed to parse pairing response");
        return ESP_FAIL;
    }

    return ESP_OK;
}
