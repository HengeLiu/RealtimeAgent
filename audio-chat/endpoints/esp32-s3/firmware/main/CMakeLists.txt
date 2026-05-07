idf_component_register(
    SRCS "audio_chat_reference_main.c"
    INCLUDE_DIRS "."
    REQUIRES esp_event esp_netif esp_timer esp_wifi esp_websocket_client json nvs_flash
)
