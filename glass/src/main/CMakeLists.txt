idf_component_register(
    SRCS "glass_main.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_event esp_netif esp_psram esp_timer esp_wifi esp_websocket_client json nvs_flash
)
