# ESP32-S3 reference endpoint

ESP32-S3 端侧仍以后续真机固件为准。本目录只记录当前 server SDK 期待的端侧协议：

1. 端侧完成唤醒词后，再向 server 发送 `control.user.wake.detected`。
2. 收到 `control.audio_session.open.requested` 后，才打开 `sensor.mic` stream。
3. AEC、NS、AGC、麦克风采样、喇叭播放和硬件时钟都在端侧完成。
4. server 只处理 `/ws/control` 事件和 `/ws/stream` 二进制 chunk。
5. 下行 `actuator.speaker` stream 播放完成后，端侧上报
   `stream.output.finished` 和 `stream.output.closed`。

已有实验桥接说明见 [../docs/esp32-s3-endpoint-bridge.md](../../docs/esp32-s3-endpoint-bridge.md)。
