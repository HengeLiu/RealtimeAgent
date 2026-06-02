# ESP32-S3 Device Demo

该目录是基于 `devices/c` 的 ESP32-S3 端侧参考实现，用于后续真机验证联网、注册、实时对话、speaker 下行、RGB 单帧、WakeNet 和 AEC。

当前代码处于第一版固件骨架阶段：

- 已接入 C Device SDK 公共 API。
- 已提供 ESP-IDF 工程结构、board config、WebSocket transport adapter 和控制接收任务。
- 已提供 mic、speaker、camera、WakeNet、AEC adapter 的边界文件。
- 默认音频 adapter 采用占位实现，避免在未知板卡上硬编码 I2S/PDM 行为；真机前需要按实际板卡补齐或启用硬件实现。

## 配置

复制 `local.env.example` 到本地环境或通过 `menuconfig` / `sdkconfig.defaults` 设置：

```text
REALTIME_AGENT_SERVER_URL=http://192.168.10.10:8765
REALTIME_AGENT_USER_ID=user-device-demo
REALTIME_AGENT_DEVICE_ID=dev-esp32-s3-001
REALTIME_AGENT_WIFI_SSID=<your wifi>
REALTIME_AGENT_WIFI_PASSWORD=<your password>
```

不要提交真实 Wi-Fi、token 或本地地址配置。

## 构建

```bash
cd examples/device_app_demo/esp32-s3/firmware
idf.py set-target esp32s3
idf.py build
```

## 真机 smoke 观察点

串口日志至少应出现：

```text
wifi.connected ip=<ip>
control.connected
device.registered
audio_session.open.requested
audio_session.opened
speaker.output.start
speaker.finished
audio_session.closed
```

当前第一版更适合先验证 Wi-Fi、WebSocket、注册和事件路由。完整音频、相机、WakeNet、AEC 需要在确认板卡和 ESP-IDF 版本后继续补硬件实现。
