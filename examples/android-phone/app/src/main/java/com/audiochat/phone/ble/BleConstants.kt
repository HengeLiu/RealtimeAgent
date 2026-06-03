package com.audiochat.phone.ble

import java.util.UUID

object BleConstants {
    // Service UUID: 12345678-1234-5678-1234-56789abcdef0
    val SERVICE_UUID: UUID = UUID.fromString("12345678-1234-5678-1234-56789abcdef0")

    // Characteristic UUIDs
    val CHAR_SSID: UUID = UUID.fromString("12345678-1234-5678-1234-56789abcdef1")
    val CHAR_PASS: UUID = UUID.fromString("12345678-1234-5678-1234-56789abcdef2")
    val CHAR_SERVER: UUID = UUID.fromString("12345678-1234-5678-1234-56789abcdef4")
    val CHAR_STATUS: UUID = UUID.fromString("12345678-1234-5678-1234-56789abcdef5")

    // Client Characteristic Configuration Descriptor
    val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    // Scan timeout
    const val SCAN_TIMEOUT_MS = 30_000L

    // Device name prefix
    const val DEVICE_NAME_PREFIX = "Glass-"
}
