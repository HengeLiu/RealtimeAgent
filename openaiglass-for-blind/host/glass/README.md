# host/glass

本目录放盲人 AI 眼镜眼镜端宿主配置和硬件说明。通用 ESP32 眼镜 SDK 运行时已经移到 [../../../openaiglass-sdk/glass-esp32](../../../openaiglass-sdk/glass-esp32)，负责传感器采集、控制连接、音频播放和端侧执行。

眼镜端启动、构建和烧录脚本统一放在 [../../scripts](../../scripts)，当前入口是：

```bash
bash scripts/run_glass.sh
```
