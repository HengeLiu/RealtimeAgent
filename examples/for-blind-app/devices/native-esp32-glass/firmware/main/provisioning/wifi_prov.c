#include "wifi_prov.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "cJSON.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include <string.h>
#include <stdlib.h>

static const char *TAG = "wifi_prov";

static wifi_prov_state_t s_state = WIFI_PROV_IDLE;
static wifi_prov_cred_cb_t s_cred_cb = NULL;
static httpd_handle_t s_server = NULL;
static esp_netif_t *s_ap_netif = NULL;

// DNS server for captive portal redirect
static TaskHandle_t s_dns_task = NULL;

// Credentials
static char s_ssid[33] = {0};
static char s_pass[65] = {0};
static char s_server_host[64] = {0};
static uint16_t s_server_port = 9000;

// HTML config page
static const char CONFIG_PAGE[] =
    "<!DOCTYPE html><html><head>"
    "<meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Glass Setup</title>"
    "<style>"
    "body{font-family:-apple-system,sans-serif;max-width:400px;margin:40px auto;padding:0 20px;background:#f5f5f5}"
    "h1{font-size:1.4em;text-align:center;color:#333}"
    ".card{background:#fff;border-radius:12px;padding:24px;box-shadow:0 2px 8px rgba(0,0,0,.1)}"
    "label{display:block;margin-bottom:4px;font-weight:600;color:#555;font-size:.9em}"
    "input{width:100%;padding:12px;border:1px solid #ddd;border-radius:8px;font-size:16px;box-sizing:border-box;margin-bottom:16px}"
    "input:focus{outline:none;border-color:#4a90d9}"
    "button{width:100%;padding:14px;background:#4a90d9;color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:600;cursor:pointer}"
    "button:hover{background:#3a7bc8}"
    ".hint{font-size:.8em;color:#999;margin-top:8px}"
    "</style></head><body>"
    "<h1>Glass Device Setup</h1>"
    "<div class='card'>"
    "<form method='POST' action='/submit'>"
    "<label>WiFi Network Name</label>"
    "<input name='ssid' placeholder='Your WiFi name' required>"
    "<label>WiFi Password</label>"
    "<input name='pass' type='password' placeholder='WiFi password'>"
    "<label>Server Address</label>"
    "<input name='server' placeholder='192.168.31.8:9000' value='192.168.31.8:9000'>"
    "<button type='submit'>Connect</button>"
    "<p class='hint'>Device will restart after saving.</p>"
    "</form></div></body></html>";

static const char SUCCESS_PAGE[] =
    "<!DOCTYPE html><html><head>"
    "<meta charset='UTF-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>Connected</title>"
    "<style>"
    "body{font-family:-apple-system,sans-serif;max-width:400px;margin:60px auto;padding:0 20px;text-align:center;background:#f5f5f5}"
    ".card{background:#fff;border-radius:12px;padding:40px;box-shadow:0 2px 8px rgba(0,0,0,.1)}"
    ".check{font-size:64px;color:#4CAF50}"
    "h2{color:#333}p{color:#666}"
    "</style></head><body>"
    "<div class='card'>"
    "<div class='check'>&#10003;</div>"
    "<h2>Saved!</h2>"
    "<p>Device is connecting to WiFi and restarting...</p>"
    "</div></body></html>";

// URL decode (handles %XX and +)
static void url_decode(char *dst, const char *src, size_t dst_size) {
    size_t di = 0;
    for (size_t si = 0; src[si] && di < dst_size - 1; si++) {
        if (src[si] == '%') {
            if (src[si+1] && src[si+2]) {
                char hex[3] = {src[si+1], src[si+2], 0};
                dst[di++] = (char)strtol(hex, NULL, 16);
                si += 2;
            }
        } else if (src[si] == '+') {
            dst[di++] = ' ';
        } else {
            dst[di++] = src[si];
        }
    }
    dst[di] = '\0';
}

// Parse form-urlencoded body: ssid=...&pass=...&server=...
static void parse_form_data(const char *body) {
    char key[64], val[128];
    const char *p = body;

    s_ssid[0] = '\0';
    s_pass[0] = '\0';
    s_server_host[0] = '\0';
    s_server_port = 9000;

    while (*p) {
        // Extract key
        size_t ki = 0;
        while (*p && *p != '=' && *p != '&' && ki < sizeof(key) - 1) {
            key[ki++] = *p++;
        }
        key[ki] = '\0';

        if (*p == '=') p++;

        // Extract value
        size_t vi = 0;
        while (*p && *p != '&' && vi < sizeof(val) - 1) {
            val[vi++] = *p++;
        }
        val[vi] = '\0';

        if (*p == '&') p++;

        // Decode and store
        char decoded[128];
        url_decode(decoded, val, sizeof(decoded));

        if (strcmp(key, "ssid") == 0) {
            strncpy(s_ssid, decoded, sizeof(s_ssid) - 1);
        } else if (strcmp(key, "pass") == 0) {
            strncpy(s_pass, decoded, sizeof(s_pass) - 1);
        } else if (strcmp(key, "server") == 0) {
            char *colon = strchr(decoded, ':');
            if (colon) {
                size_t hlen = colon - decoded;
                if (hlen >= sizeof(s_server_host)) hlen = sizeof(s_server_host) - 1;
                strncpy(s_server_host, decoded, hlen);
                s_server_host[hlen] = '\0';
                s_server_port = atoi(colon + 1);
            } else {
                strncpy(s_server_host, decoded, sizeof(s_server_host) - 1);
            }
        }
    }

    ESP_LOGI(TAG, "Parsed: ssid=%.3s*** server=%s:%d", s_ssid, s_server_host, s_server_port);
}

// Serve config page for any unmatched URL (captive portal)
static esp_err_t config_page_handler(httpd_req_t *req) {
    // Set headers for captive portal detection
    httpd_resp_set_hdr(req, "Cache-Control", "no-cache, no-store, must-revalidate");
    httpd_resp_set_type(req, "text/html");
    return httpd_resp_send(req, CONFIG_PAGE, sizeof(CONFIG_PAGE) - 1);
}

// Handle form submission
static esp_err_t submit_handler(httpd_req_t *req) {
    char buf[512];
    int ret = httpd_req_recv(req, buf, sizeof(buf) - 1);
    if (ret <= 0) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Empty body");
        return ESP_FAIL;
    }
    buf[ret] = '\0';

    parse_form_data(buf);

    if (s_ssid[0] == '\0') {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "SSID required");
        return ESP_FAIL;
    }

    // Show success page
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, SUCCESS_PAGE, sizeof(SUCCESS_PAGE) - 1);

    // Signal credentials received
    s_state = WIFI_PROV_CRED_RECEIVED;
    if (s_cred_cb) {
        s_cred_cb(s_ssid, s_pass, s_server_host, s_server_port);
    }

    return ESP_OK;
}

// Captive portal redirect handler (for OS detection URLs)
static esp_err_t captive_redirect_handler(httpd_req_t *req) {
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/");
    httpd_resp_set_hdr(req, "Cache-Control", "no-cache");
    httpd_resp_send(req, NULL, 0);
    return ESP_OK;
}

// Minimal DNS server that resolves everything to 192.168.4.1
static void dns_server_task(void *pvParameters) {
    ESP_LOGI(TAG, "DNS server task started");

    struct sockaddr_in server_addr;
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "DNS socket create failed");
        vTaskDelete(NULL);
        return;
    }

    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(53);
    server_addr.sin_addr.s_addr = htonl(INADDR_ANY);

    if (bind(sock, (struct sockaddr *)&server_addr, sizeof(server_addr)) < 0) {
        ESP_LOGE(TAG, "DNS bind failed");
        close(sock);
        vTaskDelete(NULL);
        return;
    }

    // Set socket timeout so we can check s_state periodically
    struct timeval tv = { .tv_sec = 1, .tv_usec = 0 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint8_t rx_buf[512];
    uint8_t tx_buf[512];

    while (s_state == WIFI_PROV_AP_ACTIVE) {
        struct sockaddr_in client_addr;
        socklen_t addr_len = sizeof(client_addr);
        int len = recvfrom(sock, rx_buf, sizeof(rx_buf), 0,
                          (struct sockaddr *)&client_addr, &addr_len);
        if (len < 12) continue;

        // Build DNS response: copy header, set QR=1, ANCOUNT=1
        memcpy(tx_buf, rx_buf, len);
        tx_buf[2] = 0x81; tx_buf[3] = 0x80; // Standard response, no error
        tx_buf[6] = 0x00; tx_buf[7] = 0x01; // ANCOUNT = 1

        // Append answer: name pointer to query, type A, class IN, TTL 60s, IP 192.168.4.1
        int tx_len = len;
        // Name pointer (offset 0x000c = question section)
        tx_buf[tx_len++] = 0xC0; tx_buf[tx_len++] = 0x0C;
        // Type A
        tx_buf[tx_len++] = 0x00; tx_buf[tx_len++] = 0x01;
        // Class IN
        tx_buf[tx_len++] = 0x00; tx_buf[tx_len++] = 0x01;
        // TTL 60 seconds
        tx_buf[tx_len++] = 0x00; tx_buf[tx_len++] = 0x00;
        tx_buf[tx_len++] = 0x00; tx_buf[tx_len++] = 0x3C;
        // RDLENGTH = 4
        tx_buf[tx_len++] = 0x00; tx_buf[tx_len++] = 0x04;
        // RDATA = 192.168.4.1
        tx_buf[tx_len++] = 192; tx_buf[tx_len++] = 168;
        tx_buf[tx_len++] = 4;   tx_buf[tx_len++] = 1;

        sendto(sock, tx_buf, tx_len, 0,
               (struct sockaddr *)&client_addr, addr_len);
    }

    close(sock);
    ESP_LOGI(TAG, "DNS server task exiting");
    s_dns_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t wifi_prov_start(const char *device_name) {
    if (s_state != WIFI_PROV_IDLE && s_state != WIFI_PROV_ERROR) {
        ESP_LOGW(TAG, "WiFi provisioning already active (state=%d)", s_state);
        return ESP_OK;
    }

    ESP_LOGI(TAG, "Starting WiFi AP provisioning: %s", device_name);

    // Ensure netif and event loop are initialized
    esp_err_t ret = esp_netif_init();
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "esp_netif_init failed: %d", ret);
        return ret;
    }
    ret = esp_event_loop_create_default();
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "esp_event_loop_create_default failed: %d", ret);
        return ret;
    }

    // Create AP netif
    s_ap_netif = esp_netif_create_default_wifi_ap();

    // Configure AP
    wifi_config_t ap_config = {
        .ap = {
            .max_connection = 4,
            .authmode = WIFI_AUTH_OPEN,
            .channel = 1,
        },
    };
    strncpy((char *)ap_config.ap.ssid, device_name, sizeof(ap_config.ap.ssid) - 1);
    ap_config.ap.ssid_len = strlen(device_name);

    // Init WiFi in AP mode
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ret = esp_wifi_init(&cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "WiFi init failed: %d", ret);
        return ret;
    }

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "WiFi AP started: %s (open, channel 1)", device_name);

    // Start HTTP server
    httpd_config_t http_config = HTTPD_DEFAULT_CONFIG();
    http_config.max_uri_handlers = 8;
    http_config.stack_size = 8192;
    http_config.lru_purge_enable = true;

    ret = httpd_start(&s_server, &http_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "HTTP server start failed: %d", ret);
        return ret;
    }

    // Register handlers
    // Captive portal: redirect OS detection URLs
    httpd_uri_t redirect_uri = {
        .uri = "/generate_204",
        .method = HTTP_GET,
        .handler = captive_redirect_handler,
    };
    httpd_register_uri_handler(s_server, &redirect_uri);

    httpd_uri_t redirect_uri2 = {
        .uri = "/hotspot-detect.html",
        .method = HTTP_GET,
        .handler = captive_redirect_handler,
    };
    httpd_register_uri_handler(s_server, &redirect_uri2);

    httpd_uri_t redirect_uri3 = {
        .uri = "/connecttest.txt",
        .method = HTTP_GET,
        .handler = captive_redirect_handler,
    };
    httpd_register_uri_handler(s_server, &redirect_uri3);

    httpd_uri_t redirect_uri4 = {
        .uri = "/ncsi.txt",
        .method = HTTP_GET,
        .handler = captive_redirect_handler,
    };
    httpd_register_uri_handler(s_server, &redirect_uri4);

    httpd_uri_t redirect_uri5 = {
        .uri = "/success.txt",
        .method = HTTP_GET,
        .handler = captive_redirect_handler,
    };
    httpd_register_uri_handler(s_server, &redirect_uri5);

    // Config page (also serves as catch-all for captive portal)
    httpd_uri_t config_uri = {
        .uri = "/",
        .method = HTTP_GET,
        .handler = config_page_handler,
    };
    httpd_register_uri_handler(s_server, &config_uri);

    // Form submission
    httpd_uri_t submit_uri = {
        .uri = "/submit",
        .method = HTTP_POST,
        .handler = submit_handler,
    };
    httpd_register_uri_handler(s_server, &submit_uri);

    // Start DNS server task
    s_state = WIFI_PROV_AP_ACTIVE;
    xTaskCreatePinnedToCore(&dns_server_task, "dns_task", 4096, NULL, 5, &s_dns_task, 0);

    ESP_LOGI(TAG, "Captive portal ready at http://192.168.4.1/");
    return ESP_OK;
}

esp_err_t wifi_prov_stop(void) {
    ESP_LOGI(TAG, "Stopping WiFi AP provisioning");

    // Stop HTTP server
    if (s_server) {
        httpd_stop(s_server);
        s_server = NULL;
    }

    // Stop and deinit WiFi driver
    esp_wifi_stop();
    esp_wifi_deinit();

    // Destroy AP netif (STA netif will be created by provisioning.c)
    if (s_ap_netif) {
        esp_netif_destroy(s_ap_netif);
        s_ap_netif = NULL;
    }

    // Note: esp_netif_init() and esp_event_loop_create_default() are NOT undone
    // because they are one-time inits that provisioning.c still needs.

    s_state = WIFI_PROV_IDLE;
    ESP_LOGI(TAG, "WiFi AP provisioning stopped");
    return ESP_OK;
}

wifi_prov_state_t wifi_prov_get_state(void) {
    return s_state;
}

void wifi_prov_set_cred_callback(wifi_prov_cred_cb_t cb) {
    s_cred_cb = cb;
}
