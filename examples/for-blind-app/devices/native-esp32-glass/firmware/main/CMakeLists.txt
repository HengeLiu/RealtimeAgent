idf_component_register(
    SRCS
        "realtime_agent_reference_main.c"
        "../../../../../../audio-device/c/src/realtime_agent_device.c"
        "../../../../../../audio-device/c/src/realtime_agent_stream.c"
    INCLUDE_DIRS "." "../../../../../../audio-device/c/include"
    REQUIRES esp_event esp_netif esp_timer esp_wifi esp_websocket_client json nvs_flash
)
