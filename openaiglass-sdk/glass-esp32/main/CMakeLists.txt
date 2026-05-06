set(APP_SRCS "glass_main.c")

if(CONFIG_GLASS_ENABLE_WAKENET_TEST_APP)
    set(APP_SRCS "test_wakenet.c")
endif()

if(CONFIG_GLASS_ENABLE_OFFICIAL_AEC_TEST_APP)
    set(APP_SRCS "test_official_aec.c")
endif()

idf_component_register(
    SRCS ${APP_SRCS}
    INCLUDE_DIRS "."
    REQUIRES driver esp_event esp_http_client esp_netif esp_psram esp_timer esp_wifi esp_websocket_client json nvs_flash
)
