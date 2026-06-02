#include "wifi_manager.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "nvs_flash.h"

static const char *TAG = "wifi_manager";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1

static EventGroupHandle_t s_wifi_event_group = NULL;
static wifi_state_t s_wifi_state = WIFI_STATE_IDLE;
static char s_local_ip[32] = {0};
static wifi_on_connected_cb_t s_on_connected = NULL;
static wifi_on_disconnected_cb_t s_on_disconnected = NULL;
static int s_retry_count = 0;
static const int s_max_retries = 10;
static bool s_initialized = false;
static esp_netif_t *s_sta_netif = NULL;
static esp_netif_t *s_ap_netif = NULL;

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                                int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retry_count < s_max_retries) {
            esp_wifi_connect();
            s_retry_count++;
            ESP_LOGI(TAG, "retry to connect to AP (attempt %d)", s_retry_count);
        } else {
            s_wifi_state = WIFI_STATE_FAILED;
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
        ESP_LOGI(TAG, "connected to AP failed");
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));
        s_retry_count = 0;
        s_wifi_state = WIFI_STATE_CONNECTED;
        snprintf(s_local_ip, sizeof(s_local_ip), IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        if (s_on_connected) {
            s_on_connected();
        }
    }
}

esp_err_t wifi_manager_init(const char *ssid, const char *password) {
    s_wifi_event_group = xEventGroupCreate();

    if (!s_initialized) {
        ESP_ERROR_CHECK(esp_netif_init());
        ESP_ERROR_CHECK(esp_event_loop_create_default());
        s_initialized = true;
    }

    if (!s_sta_netif) {
        s_sta_netif = esp_netif_create_default_wifi_sta();
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL));

    wifi_config_t wifi_config = {0};
    strncpy((char*)wifi_config.sta.ssid, ssid, sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char*)wifi_config.sta.password, password, sizeof(wifi_config.sta.password) - 1);
    // rma_adaptive_enable removed in ESP-IDF 6.x

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    s_wifi_state = WIFI_STATE_CONNECTING;
    ESP_ERROR_CHECK(esp_wifi_start());

    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                           WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE, portMAX_DELAY);

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "connected to AP %s", ssid);
        return ESP_OK;
    } else if (bits & WIFI_FAIL_BIT) {
        ESP_LOGI(TAG, "Failed to connect to AP %s", ssid);
        return ESP_FAIL;
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t wifi_manager_set_callbacks(wifi_on_connected_cb_t on_connected,
                                      wifi_on_disconnected_cb_t on_disconnected) {
    s_on_connected = on_connected;
    s_on_disconnected = on_disconnected;
    return ESP_OK;
}

wifi_state_t wifi_manager_get_state(void) {
    return s_wifi_state;
}

bool wifi_manager_is_connected(void) {
    return s_wifi_state == WIFI_STATE_CONNECTED;
}

const char* wifi_manager_get_local_ip(void) {
    return s_local_ip;
}

esp_err_t wifi_manager_start_ap(const char *ssid) {
    if (!s_initialized) {
        ESP_ERROR_CHECK(esp_netif_init());
        ESP_ERROR_CHECK(esp_event_loop_create_default());
        s_initialized = true;
    }

    if (!s_ap_netif) {
        s_ap_netif = esp_netif_create_default_wifi_ap();
    }

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_err_t ret = esp_wifi_init(&cfg);
    if (ret != ESP_OK && ret != ESP_ERR_WIFI_INIT_STATE) {
        ESP_LOGE(TAG, "WiFi init for AP failed: %d", ret);
        return ret;
    }

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));

    wifi_config_t ap_config = {0};
    strncpy((char *)ap_config.ap.ssid, ssid, sizeof(ap_config.ap.ssid) - 1);
    ap_config.ap.ssid_len = strlen(ssid);
    ap_config.ap.max_connection = 4;
    ap_config.ap.authmode = WIFI_AUTH_OPEN;
    ap_config.ap.channel = 1;

    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "AP started: %s (open, channel 1)", ssid);
    return ESP_OK;
}

esp_err_t wifi_manager_stop_ap(void) {
    esp_wifi_stop();
    esp_wifi_set_mode(WIFI_MODE_NULL);
    ESP_LOGI(TAG, "AP stopped");
    return ESP_OK;
}

esp_err_t wifi_manager_scan_and_connect(const char *ssid, const char *password) {
    s_retry_count = 0;
    s_wifi_state = WIFI_STATE_CONNECTING;

    // Disconnect any existing STA connection
    esp_wifi_disconnect();
    vTaskDelay(pdMS_TO_TICKS(100));

    wifi_config_t sta_config = {0};
    strncpy((char *)sta_config.sta.ssid, ssid, sizeof(sta_config.sta.ssid) - 1);
    strncpy((char *)sta_config.sta.password, password, sizeof(sta_config.sta.password) - 1);

    // Switch to APSTA mode to keep AP running while connecting STA
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_APSTA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_config));
    ESP_ERROR_CHECK(esp_wifi_connect());

    ESP_LOGI(TAG, "STA connecting to: %s", ssid);

    // Wait for connection result
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                           WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE, pdMS_TO_TICKS(15000));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "STA connected to %s, IP: %s", ssid, s_local_ip);
        return ESP_OK;
    }

    ESP_LOGW(TAG, "STA failed to connect to %s", ssid);
    s_wifi_state = WIFI_STATE_FAILED;
    return ESP_FAIL;
}