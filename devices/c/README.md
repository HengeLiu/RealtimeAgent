# C Device SDK

`devices/c` 是 realtime-agent 的 C 语言端侧 SDK。它负责协议编解码、注册 payload、基础状态机、speaker buffer 和硬件/transport adapter 接口；具体硬件实现由 ESP32-S3 demo、嵌入式 Linux app 或其他 BSP 提供。

当前版本是第一版实现骨架，重点覆盖无硬件可验证能力：

- `Event` JSON 编码和基础解码。
- `StreamChunk` 二进制编码和解码。
- 注册 payload 生成，避免旧 `routes` / `capabilities`。
- speaker buffer 的乱序、重复、cancel 和水位线基础行为。
- client 创建、注册事件生成和对部分 server 控制事件的同步处理。

## 本地构建

```bash
cmake -S devices/c -B /tmp/realtime-agent-device-c-build
cmake --build /tmp/realtime-agent-device-c-build
ctest --test-dir /tmp/realtime-agent-device-c-build --output-on-failure
```

## 边界

C SDK 核心不包含 ESP32-S3 引脚、I2S/PDM/camera 初始化、Wi-Fi、WakeNet 或 AEC。需要硬件时由应用传入：

- `ra_mic_source_t`
- `ra_camera_source_t`
- `ra_speaker_sink_t`
- `ra_transport_t`

ESP32-S3 参考实现见 `examples/device_app_demo/esp32-s3/`。
