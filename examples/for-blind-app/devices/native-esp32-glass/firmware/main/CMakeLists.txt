idf_component_register(
    SRCS
        "app/main.c"
        "app/device.c"
        "drivers/audio.c"
        "drivers/camera.c"
        "drivers/imu.c"
        "drivers/feedback_tone.c"
        "storage/config_store.c"
        "provisioning/provisioning.c"
        "provisioning/ble_prov.c"
        "connectivity/wifi_manager.c"
        "connectivity/ws_control.c"
        "connectivity/ws_stream.c"
        "protocol/protocol_adapter.c"
        "protocol/audio_chat_stream.c"
        "utils/wake_word.c"
    INCLUDE_DIRS "."
    REQUIRES esp_event esp_netif esp_timer esp_wifi esp_websocket_client nvs_flash driver fatfs esp-sr esp32-camera esp_http_server esp_http_client esp_hw_support json bt
)