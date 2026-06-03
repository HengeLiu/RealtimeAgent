#include "ble_prov.h"
#include "esp_bt.h"
#include "esp_bt_main.h"
#include "esp_gap_ble_api.h"
#include "esp_gatts_api.h"
#include "esp_log.h"
#include <string.h>

static const char *TAG = "ble_prov";

// Custom service UUID: 12345678-1234-5678-1234-56789abcdef0
static const uint8_t SERVICE_UUID[16] = {
    0xf0, 0xde, 0xbc, 0x9a, 0x78, 0x56, 0x34, 0x12,
    0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12
};

// Characteristic UUIDs (offset from service UUID)
// SSID:       ...f1
// Password:   ...f2
// ServerInfo: ...f4
// Status:     ...f5

#define CHAR_IDX_SSID       0
#define CHAR_IDX_PASS       1
#define CHAR_IDX_SERVER     2
#define CHAR_IDX_STATUS     3
#define CHAR_IDX_COUNT      4

static const uint8_t CHAR_UUIDS[CHAR_IDX_COUNT][16] = {
    {0xf1, 0xde, 0xbc, 0x9a, 0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12},
    {0xf2, 0xde, 0xbc, 0x9a, 0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12},
    {0xf4, 0xde, 0xbc, 0x9a, 0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12},
    {0xf5, 0xde, 0xbc, 0x9a, 0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12},
};

static ble_prov_state_t s_state = BLE_PROV_IDLE;
static ble_prov_cred_cb_t s_cred_cb = NULL;

// GATT handles
static uint16_t s_gatts_if = 0;
static uint16_t s_service_handle = 0;
static uint16_t s_char_handles[CHAR_IDX_COUNT] = {0};
static uint16_t s_status_cccd_handle = 0;
static uint16_t s_conn_id = 0;
static bool s_connected = false;
static bool s_status_notify_enabled = false;

// Credential storage
static char s_ssid[33] = {0};
static char s_pass[65] = {0};
static char s_server_host[64] = {0};
static uint16_t s_server_port = 8766;
static volatile bool s_cred_flags[3] = {false};  // ssid, pass, server

// Forward declarations
static void gatts_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
                                 esp_ble_gatts_cb_param_t *param);
static void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param);
static void check_credentials_complete(void);

// BLE advertising data
static esp_ble_adv_data_t s_adv_data = {
    .set_scan_rsp = false,
    .include_name = true,
    .include_txpower = false,
    .min_interval = 0x0006,
    .max_interval = 0x0010,
    .appearance = 0x00,
    .manufacturer_len = 0,
    .p_manufacturer_data = NULL,
    .service_data_len = 0,
    .p_service_data = NULL,
    .service_uuid_len = sizeof(SERVICE_UUID),
    .p_service_uuid = (uint8_t *)SERVICE_UUID,
    .flag = (ESP_BLE_ADV_FLAG_GEN_DISC | ESP_BLE_ADV_FLAG_BREDR_NOT_SPT),
};

static esp_ble_adv_params_t s_adv_params = {
    .adv_int_min = 0x00A0,  // 100ms
    .adv_int_max = 0x0140,  // 200ms
    .adv_type = ADV_TYPE_IND,
    .own_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .peer_addr = {0},
    .peer_addr_type = BLE_ADDR_TYPE_PUBLIC,
    .channel_map = ADV_CHNL_ALL,
    .adv_filter_policy = ADV_FILTER_ALLOW_SCAN_ANY_CON_ANY,
};

// 128-bit service UUID for advertising
static esp_bt_uuid_t s_service_uuid = {
    .len = ESP_UUID_LEN_128,
    .uuid = {.uuid128 = {0}},
};

// Characteristic properties
static esp_gatt_char_prop_t s_char_props_write = ESP_GATT_CHAR_PROP_BIT_WRITE;
static esp_gatt_char_prop_t s_char_props_notify = ESP_GATT_CHAR_PROP_BIT_NOTIFY | ESP_GATT_CHAR_PROP_BIT_READ;

esp_err_t ble_prov_start(const char *device_name) {
    if (s_state != BLE_PROV_IDLE && s_state != BLE_PROV_ERROR) {
        ESP_LOGW(TAG, "BLE provisioning already active (state=%d)", s_state);
        return ESP_OK;
    }

    // Reset state
    memset((void *)s_cred_flags, 0, sizeof(s_cred_flags));
    s_ssid[0] = '\0';
    s_pass[0] = '\0';
    s_server_host[0] = '\0';
    s_server_port = 8766;
    s_connected = false;
    s_status_notify_enabled = false;

    // Release classic BT memory, keep BLE
    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));

    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    esp_err_t ret = esp_bt_controller_init(&bt_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "BT controller init failed: %d", ret);
        return ret;
    }

    ret = esp_bt_controller_enable(ESP_BT_MODE_BLE);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "BT controller enable failed: %d", ret);
        return ret;
    }

    ret = esp_bluedroid_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Bluedroid init failed: %d", ret);
        return ret;
    }

    ret = esp_bluedroid_enable();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Bluedroid enable failed: %d", ret);
        return ret;
    }

    // Register GATT server callback
    ret = esp_ble_gatts_register_callback(gatts_event_handler);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "GATTS register callback failed: %d", ret);
        return ret;
    }

    // Register GAP callback
    ret = esp_ble_gap_register_callback(gap_event_handler);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "GAP register callback failed: %d", ret);
        return ret;
    }

    // Set device name
    ret = esp_ble_gap_set_device_name(device_name);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Set device name failed: %d", ret);
        return ret;
    }

    // Configure advertising data
    memcpy(s_service_uuid.uuid.uuid128, SERVICE_UUID, 16);
    ret = esp_ble_gap_config_adv_data(&s_adv_data);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Config adv data failed: %d", ret);
        return ret;
    }

    // Register GATT app (app_id=0x55)
    ret = esp_ble_gatts_app_register(0x55);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "GATTS app register failed: %d", ret);
        return ret;
    }

    s_state = BLE_PROV_ADVERTISING;
    ESP_LOGI(TAG, "BLE provisioning started, advertising as '%s'", device_name);
    return ESP_OK;
}

esp_err_t ble_prov_stop(void) {
    ESP_LOGI(TAG, "Stopping BLE provisioning");

    if (s_connected) {
        esp_ble_gatts_close(s_gatts_if, s_conn_id);
        s_connected = false;
    }

    esp_ble_gap_stop_advertising();
    esp_bluedroid_disable();
    esp_bluedroid_deinit();
    esp_bt_controller_disable();
    esp_bt_controller_deinit();

    s_state = BLE_PROV_IDLE;
    s_gatts_if = 0;
    s_service_handle = 0;
    memset(s_char_handles, 0, sizeof(s_char_handles));

    ESP_LOGI(TAG, "BLE provisioning stopped, BT released");
    return ESP_OK;
}

ble_prov_state_t ble_prov_get_state(void) {
    return s_state;
}

void ble_prov_set_cred_callback(ble_prov_cred_cb_t cb) {
    s_cred_cb = cb;
}

void ble_prov_send_status(const char *status) {
    if (!s_connected || !s_status_notify_enabled) {
        ESP_LOGW(TAG, "Cannot send status: connected=%d notify=%d",
                 s_connected, s_status_notify_enabled);
        return;
    }

    esp_ble_gatts_send_indicate(s_gatts_if, s_conn_id,
                                 s_char_handles[CHAR_IDX_STATUS],
                                 strlen(status), (uint8_t *)status, false);
    ESP_LOGI(TAG, "Status notified: %s", status);
}

static void check_credentials_complete(void) {
    if (s_cred_flags[0] && s_cred_flags[1] && s_cred_flags[2]) {
        ESP_LOGI(TAG, "All credentials received: ssid=%.3s*** server=%s:%d",
                 s_ssid, s_server_host, s_server_port);
        s_state = BLE_PROV_CRED_RECEIVED;
        if (s_cred_cb) {
            s_cred_cb(s_ssid, s_pass, s_server_host, s_server_port);
        }
    }
}

static void handle_write(uint16_t handle, const uint8_t *data, uint16_t len) {
    char buf[128] = {0};
    size_t copy_len = len < sizeof(buf) - 1 ? len : sizeof(buf) - 1;
    memcpy(buf, data, copy_len);
    buf[copy_len] = '\0';

    if (handle == s_char_handles[CHAR_IDX_SSID]) {
        strncpy(s_ssid, buf, sizeof(s_ssid) - 1);
        s_cred_flags[0] = true;
        ESP_LOGI(TAG, "SSID received: %.3s***", s_ssid);
    }
    else if (handle == s_char_handles[CHAR_IDX_PASS]) {
        strncpy(s_pass, buf, sizeof(s_pass) - 1);
        s_cred_flags[1] = true;
        ESP_LOGI(TAG, "Password received (%d bytes)", (int)len);
    }
    else if (handle == s_char_handles[CHAR_IDX_SERVER]) {
        // Parse "host:port"
        char *colon = strchr(buf, ':');
        if (colon) {
            size_t host_len = colon - buf;
            if (host_len >= sizeof(s_server_host)) host_len = sizeof(s_server_host) - 1;
            strncpy(s_server_host, buf, host_len);
            s_server_host[host_len] = '\0';
            s_server_port = atoi(colon + 1);
        } else {
            strncpy(s_server_host, buf, sizeof(s_server_host) - 1);
        }
        s_cred_flags[2] = true;
        ESP_LOGI(TAG, "Server info received: %s:%d", s_server_host, s_server_port);
    }

    check_credentials_complete();
}

static void gatts_event_handler(esp_gatts_cb_event_t event, esp_gatt_if_t gatts_if,
                                 esp_ble_gatts_cb_param_t *param) {
    switch (event) {
    case ESP_GATTS_REG_EVT:
        if (param->reg.status != ESP_GATT_OK) {
            ESP_LOGE(TAG, "GATTS reg failed: %d", param->reg.status);
            s_state = BLE_PROV_ERROR;
            break;
        }
        s_gatts_if = gatts_if;

        // Create service
        esp_gatt_srvc_id_t service_id = {
            .is_primary = true,
            .id = {
                .uuid = s_service_uuid,
                .inst_id = 0,
            },
        };
        esp_ble_gatts_create_service(gatts_if, &service_id, 20);
        break;

    case ESP_GATTS_CREATE_EVT:
        s_service_handle = param->create.service_handle;
        ESP_LOGI(TAG, "Service created, handle=%d", s_service_handle);

        esp_ble_gatts_start_service(s_service_handle);

        // Add characteristics
        for (int i = 0; i < CHAR_IDX_COUNT; i++) {
            esp_bt_uuid_t char_uuid = {
                .len = ESP_UUID_LEN_128,
            };
            memcpy(char_uuid.uuid.uuid128, CHAR_UUIDS[i], 16);

            esp_gatt_char_prop_t props;

            if (i == CHAR_IDX_STATUS) {
                props = s_char_props_notify;
            } else {
                props = s_char_props_write;
            }

            // Initial empty value (Bluedroid requires non-NULL)
            static uint8_t empty_val[1] = {0};
            esp_attr_value_t attr_val = {
                .attr_max_len = 256,
                .attr_len = 0,
                .attr_value = empty_val,
            };

            esp_attr_control_t control = {
                .auto_rsp = ESP_GATT_AUTO_RSP,
            };

            esp_ble_gatts_add_char(s_service_handle, &char_uuid,
                                    ESP_GATT_PERM_WRITE | ESP_GATT_PERM_READ,
                                    props, &attr_val, &control);
        }
        break;

    case ESP_GATTS_ADD_CHAR_EVT:
        if (param->add_char.status != ESP_GATT_OK) {
            ESP_LOGE(TAG, "Add char failed: %d", param->add_char.status);
            break;
        }

        // Find which characteristic was added by UUID
        for (int i = 0; i < CHAR_IDX_COUNT; i++) {
            if (memcmp(param->add_char.char_uuid.uuid.uuid128, CHAR_UUIDS[i], 16) == 0) {
                s_char_handles[i] = param->add_char.attr_handle;
                ESP_LOGI(TAG, "Char %d added, handle=%d", i, s_char_handles[i]);

                // Add CCCD descriptor for Status characteristic
                if (i == CHAR_IDX_STATUS) {
                    esp_bt_uuid_t cccd_uuid = {
                        .len = ESP_UUID_LEN_16,
                        .uuid = {.uuid16 = ESP_GATT_UUID_CHAR_CLIENT_CONFIG},
                    };
                    static uint8_t cccd_val[2] = {0, 0};
                    esp_attr_value_t cccd_attr_val = {
                        .attr_max_len = 2,
                        .attr_len = 2,
                        .attr_value = cccd_val,
                    };
                    esp_attr_control_t control = {
                        .auto_rsp = ESP_GATT_AUTO_RSP,
                    };
                    esp_ble_gatts_add_char_descr(s_service_handle, &cccd_uuid,
                                                  ESP_GATT_PERM_WRITE | ESP_GATT_PERM_READ,
                                                  &cccd_attr_val, &control);
                }
                break;
            }
        }
        break;

    case ESP_GATTS_ADD_CHAR_DESCR_EVT:
        s_status_cccd_handle = param->add_char_descr.attr_handle;
        ESP_LOGI(TAG, "CCCD descriptor added, handle=%d", s_status_cccd_handle);
        break;

    case ESP_GATTS_START_EVT:
        ESP_LOGI(TAG, "Service started");
        // Start advertising
        esp_ble_gap_start_advertising(&s_adv_params);
        break;

    case ESP_GATTS_CONNECT_EVT:
        ESP_LOGI(TAG, "BLE client connected, conn_id=%d", param->connect.conn_id);
        s_conn_id = param->connect.conn_id;
        s_connected = true;
        s_gatts_if = gatts_if;
        s_state = BLE_PROV_CONNECTED;

        // Update connection parameters
        esp_ble_conn_update_params_t conn_params = {0};
        memcpy(conn_params.bda, param->connect.remote_bda, sizeof(esp_bd_addr_t));
        conn_params.latency = 0;
        conn_params.max_int = 0x20;    // 40ms
        conn_params.min_int = 0x10;    // 20ms
        conn_params.timeout = 400;     // 4000ms
        esp_ble_gap_update_conn_params(&conn_params);
        break;

    case ESP_GATTS_DISCONNECT_EVT:
        ESP_LOGI(TAG, "BLE client disconnected, reason=0x%x", param->disconnect.reason);
        s_connected = false;
        s_status_notify_enabled = false;
        s_state = BLE_PROV_ADVERTISING;

        // Restart advertising
        esp_ble_gap_start_advertising(&s_adv_params);
        break;

    case ESP_GATTS_WRITE_EVT:
        if (!param->write.is_prep) {
            handle_write(param->write.handle, param->write.value, param->write.len);
        }

        // Check if CCCD write for Status characteristic
        if (param->write.handle == s_status_cccd_handle) {
            uint16_t cccd_val = param->write.value[0] | (param->write.value[1] << 8);
            s_status_notify_enabled = (cccd_val == 0x0001);
            ESP_LOGI(TAG, "Status notify %s", s_status_notify_enabled ? "enabled" : "disabled");
        }

        // Send response
        if (param->write.need_rsp) {
            esp_gatt_rsp_t rsp = {0};
            rsp.attr_value.handle = param->write.handle;
            rsp.attr_value.len = 0;
            esp_ble_gatts_send_response(gatts_if, param->write.conn_id,
                                         param->write.trans_id, ESP_GATT_OK, &rsp);
        }
        break;

    case ESP_GATTS_READ_EVT:
        ESP_LOGI(TAG, "Read handle=%d", param->read.handle);
        break;

    default:
        break;
    }
}

static void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
    switch (event) {
    case ESP_GAP_BLE_ADV_DATA_RAW_SET_COMPLETE_EVT:
        ESP_LOGI(TAG, "Adv data set, starting advertising...");
        esp_ble_gap_start_advertising(&s_adv_params);
        break;

    case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:
        if (param->adv_start_cmpl.status != ESP_BT_STATUS_SUCCESS) {
            ESP_LOGE(TAG, "Advertising start failed: %d", param->adv_start_cmpl.status);
            s_state = BLE_PROV_ERROR;
        } else {
            ESP_LOGI(TAG, "Advertising started");
        }
        break;

    case ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT:
        ESP_LOGI(TAG, "Advertising stopped");
        break;

    default:
        break;
    }
}
