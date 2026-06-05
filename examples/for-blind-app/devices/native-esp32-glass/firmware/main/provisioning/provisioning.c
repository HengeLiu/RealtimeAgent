#include "provisioning.h"
#include "wifi_prov.h"
#include "storage/config_store.h"
#include "connectivity/wifi_manager.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_http_client.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "cJSON.h"
#include <string.h>
#include <stdio.h>

static const char *TAG = "provisioning";

static provisioning_state_t s_state = PROV_STATE_IDLE;
static device_config_t s_config;
static TaskHandle_t s_prov_task = NULL;

// Credentials received from WiFi AP portal (set in callback, consumed in task)
static volatile bool s_cred_received = false;
static char s_cred_ssid[33];
static char s_cred_pass[65];
static char s_cred_server[64];
static uint16_t s_cred_port = 9000;

static char s_ap_ssid[32];

// Forward declarations
static void prov_task(void *pvParameters);
static void on_wifi_credentials(const char *ssid, const char *pass,
                                const char *server_host, uint16_t server_port);
static esp_err_t call_register_api(const char *server_host, uint16_t server_port,
                                    const char *hw_id,
                                    char *out_device_id, size_t device_id_size,
                                    char *out_token, size_t token_size);

esp_err_t provisioning_start(const device_config_t *config) {
    if (s_state != PROV_STATE_IDLE && s_state != PROV_STATE_ERROR) {
        ESP_LOGW(TAG, "Provisioning already in progress (state=%d)", s_state);
        return ESP_OK;
    }

    memcpy(&s_config, config, sizeof(s_config));

    // Generate AP name from hardware ID
    snprintf(s_ap_ssid, sizeof(s_ap_ssid), "Glass-%s", config->hw_id + 3); // skip "hw-"

    s_cred_received = false;
    s_state = PROV_STATE_WAITING_CREDENTIALS;

    xTaskCreatePinnedToCore(&prov_task, "prov_task", 8192, NULL, 5, &s_prov_task, 0);

    ESP_LOGI(TAG, "WiFi AP provisioning started, SSID: %s", s_ap_ssid);
    return ESP_OK;
}

esp_err_t provisioning_stop(void) {
    wifi_prov_stop();
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

static void on_wifi_credentials(const char *ssid, const char *pass,
                                const char *server_host, uint16_t server_port) {
    strncpy(s_cred_ssid, ssid, sizeof(s_cred_ssid) - 1);
    strncpy(s_cred_pass, pass, sizeof(s_cred_pass) - 1);
    strncpy(s_cred_server, server_host, sizeof(s_cred_server) - 1);
    s_cred_port = server_port;
    s_cred_received = true;

    ESP_LOGI(TAG, "WiFi credentials received: ssid=%.3s*** server=%s:%d",
             s_cred_ssid, s_cred_server, s_cred_port);
}

static void prov_task(void *pvParameters) {
    (void)pvParameters;

    ESP_LOGI(TAG, "prov_task started, free heap: %lu", (unsigned long)esp_get_free_heap_size());

    // Step 1: Start WiFi AP with captive portal
    wifi_prov_set_cred_callback(on_wifi_credentials);
    ESP_LOGI(TAG, "Calling wifi_prov_start...");
    esp_err_t ret = wifi_prov_start(s_ap_ssid);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start WiFi AP: %d", ret);
        s_state = PROV_STATE_ERROR;
        vTaskDelete(NULL);
        return;
    }

    s_state = PROV_STATE_AP_STARTED;
    ESP_LOGI(TAG, "Waiting for WiFi credentials (timeout: 5min)...");

    // Wait for credentials from captive portal
    int wait_ticks = 0;
    const int timeout_ticks = pdMS_TO_TICKS(300000); // 5 minutes
    while (!s_cred_received) {
        vTaskDelay(pdMS_TO_TICKS(200));
        wait_ticks += pdMS_TO_TICKS(200);
        if (wait_ticks >= timeout_ticks) {
            ESP_LOGE(TAG, "WiFi credential wait timed out");
            wifi_prov_stop();
            s_state = PROV_STATE_ERROR;
            s_prov_task = NULL;
            vTaskDelete(NULL);
            return;
        }
    }

    // Step 2: Stop WiFi AP (fully deinit WiFi stack)
    ESP_LOGI(TAG, "Credentials received, stopping AP...");
    wifi_prov_stop();
    vTaskDelay(pdMS_TO_TICKS(500));
    ESP_LOGI(TAG, "WiFi AP stopped, free heap: %lu", (unsigned long)esp_get_free_heap_size());

    // Step 3: Reinit WiFi in STA mode
    // esp_netif_init() and esp_event_loop_create_default() were already called
    // in wifi_prov_start(), so we only need to create STA netif and init wifi driver.
    s_state = PROV_STATE_CONNECTING_WIFI;

    // Create STA netif (skip if already exists from previous boot)
    esp_netif_t *sta_netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (sta_netif == NULL) {
        sta_netif = esp_netif_create_default_wifi_sta();
    }
    if (sta_netif == NULL) {
        ESP_LOGE(TAG, "Failed to create STA netif");
        s_state = PROV_STATE_ERROR;
        s_prov_task = NULL;
        vTaskDelete(NULL);
        return;
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);
    ESP_LOGI(TAG, "WiFi STA initialized, free heap: %lu", (unsigned long)esp_get_free_heap_size());

    // Step 4: Connect to home WiFi
    ESP_LOGI(TAG, "Connecting to WiFi: %s", s_cred_ssid);

    bool wifi_connected = false;
    for (int attempt = 1; attempt <= 3; attempt++) {
        ESP_LOGI(TAG, "WiFi connect attempt %d/3...", attempt);
        ret = wifi_manager_scan_and_connect(s_cred_ssid, s_cred_pass);
        if (ret == ESP_OK) {
            wifi_connected = true;
            break;
        }
        ESP_LOGW(TAG, "WiFi attempt %d failed, retrying...", attempt);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }

    if (!wifi_connected) {
        ESP_LOGE(TAG, "WiFi connection failed after 3 attempts");
        s_state = PROV_STATE_ERROR;
        s_prov_task = NULL;
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "WiFi connected, IP: %s", wifi_manager_get_local_ip());

    // Wait for TCP/IP stack to stabilize
    vTaskDelay(pdMS_TO_TICKS(2000));
    ESP_LOGI(TAG, "Network ready, free heap: %lu", (unsigned long)esp_get_free_heap_size());

    // Step 5: Call register API
    s_state = PROV_STATE_PAIRING;
    ESP_LOGI(TAG, "Calling register API...");

    char device_id[64] = {0};
    char auth_token[256] = {0};

    const char *server_host = s_cred_server[0] ? s_cred_server : s_config.server_host;
    uint16_t server_port = s_cred_port ? s_cred_port : s_config.server_port;

    for (int attempt = 1; attempt <= 3; attempt++) {
        ESP_LOGI(TAG, "Register API attempt %d/3 to %s:%d...", attempt, server_host, server_port);
        ret = call_register_api(
            server_host, server_port,
            s_config.hw_id,
            device_id, sizeof(device_id),
            auth_token, sizeof(auth_token)
        );
        if (ret == ESP_OK) break;
        ESP_LOGW(TAG, "Register attempt %d failed: %d, retrying...", attempt, ret);
        vTaskDelay(pdMS_TO_TICKS(3000));
    }

    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Register API failed after 3 attempts: %d", ret);
        ESP_LOGW(TAG, "Clearing config and restarting to re-enter provisioning...");
        config_store_clear_all();
        vTaskDelay(pdMS_TO_TICKS(1000));
        esp_restart();
    }

    // Step 6: Save to NVS
    ESP_LOGI(TAG, "Registration successful! device=%s", device_id);

    strncpy(s_config.wifi_ssid, s_cred_ssid, sizeof(s_config.wifi_ssid) - 1);
    strncpy(s_config.wifi_pass, s_cred_pass, sizeof(s_config.wifi_pass) - 1);
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
        s_state = PROV_STATE_ERROR;
        s_prov_task = NULL;
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Config saved — will connect to server on reboot");
    s_state = PROV_STATE_DONE;
    ESP_LOGI(TAG, "=== PROVISIONING COMPLETE ===");

    s_prov_task = NULL;
    vTaskDelete(NULL);
}

static esp_err_t call_register_api(const char *server_host, uint16_t server_port,
                                    const char *hw_id,
                                    char *out_device_id, size_t device_id_size,
                                    char *out_token, size_t token_size) {
    char url[128];
    snprintf(url, sizeof(url), "http://%s:%d/api/device/register", server_host, server_port);

    char post_data[256];
    snprintf(post_data, sizeof(post_data),
             "{\"hardware_id\":\"%s\",\"device_name\":\"ESP32 Glass\"}",
             hw_id);

    ESP_LOGI(TAG, "HTTP POST to: %s (stack free: %lu)",
             url, (unsigned long)uxTaskGetStackHighWaterMark(NULL));

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 15000,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        ESP_LOGE(TAG, "HTTP client init failed");
        return ESP_FAIL;
    }

    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, post_data, strlen(post_data));

    esp_err_t err = esp_http_client_open(client, strlen(post_data));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP open failed: %d", err);
        esp_http_client_cleanup(client);
        return err;
    }

    int write_len = esp_http_client_write(client, post_data, strlen(post_data));
    if (write_len < 0) {
        ESP_LOGE(TAG, "HTTP write failed: %d", write_len);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "HTTP wrote %d bytes", write_len);

    int content_length = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    ESP_LOGI(TAG, "HTTP status: %d, content_length: %d", status, content_length);

    if (status != 200) {
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    char resp_buf[512] = {0};
    int read_len = esp_http_client_read(client, resp_buf, sizeof(resp_buf) - 1);
    esp_http_client_cleanup(client);

    if (read_len <= 0) {
        ESP_LOGE(TAG, "HTTP read failed: %d", read_len);
        return ESP_FAIL;
    }
    resp_buf[read_len] = '\0';
    ESP_LOGI(TAG, "Response (%d bytes): %s", read_len, resp_buf);

    cJSON *root = cJSON_Parse(resp_buf);
    if (!root) {
        ESP_LOGE(TAG, "JSON parse failed");
        return ESP_FAIL;
    }

    cJSON *device_id_item = cJSON_GetObjectItem(root, "device_id");
    cJSON *auth_token_item = cJSON_GetObjectItem(root, "auth_token");

    if (cJSON_IsString(device_id_item) && device_id_item->valuestring[0]) {
        strncpy(out_device_id, device_id_item->valuestring, device_id_size - 1);
    }
    if (cJSON_IsString(auth_token_item) && auth_token_item->valuestring[0]) {
        strncpy(out_token, auth_token_item->valuestring, token_size - 1);
    }

    cJSON_Delete(root);

    if (out_device_id[0] == '\0' || out_token[0] == '\0') {
        ESP_LOGE(TAG, "Missing device_id or auth_token");
        return ESP_FAIL;
    }

    return ESP_OK;
}
