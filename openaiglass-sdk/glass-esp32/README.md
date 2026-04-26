# glass-esp32

本目录放 ESP32 通用眼镜 SDK 运行时。它负责 WiFi、控制连接、音频、摄像头、端侧命令处理和 SDK 协议适配，不放 `find_object`、导航或其他盲人产品业务策略。

当前仍保留为 ESP-IDF 可构建工程，后续可以继续收敛为 ESP-IDF component：

```bash
PROJECT_DIR=openaiglass-sdk/glass-esp32 bash openaiglass-for-blind/scripts/run_glass.sh --build-only
```

盲人产品的眼镜宿主配置和硬件说明位于 [../../openaiglass-for-blind/host/glass](../../openaiglass-for-blind/host/glass)。
