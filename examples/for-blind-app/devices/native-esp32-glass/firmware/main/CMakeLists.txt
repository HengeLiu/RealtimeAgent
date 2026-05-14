idf_component_register(
    SRCS
        "audio_chat_reference_main.c"
        "../../../../../../audio-device/c/src/audio_chat_device.c"
        "../../../../../../audio-device/c/src/audio_chat_stream.c"
    INCLUDE_DIRS "." "../../../../../../audio-device/c/include"
    REQUIRES esp_event esp_netif esp_timer esp_wifi esp_websocket_client json nvs_flash
)
