# host/glass

本目录放盲人 AI 眼镜眼镜端宿主配置和硬件说明。通用 ESP32 眼镜 SDK 运行时已经移到 [../../../openaiglass-sdk/glass-esp32](../../../openaiglass-sdk/glass-esp32)，负责传感器采集、控制连接、音频播放和端侧执行。

眼镜端启动、构建和烧录由 SDK `openaiglass` 命令提供，当前入口是：

```bash
openaiglass glass firmware --repo-root .
```

[../../scripts/run_glass.sh](../../scripts/run_glass.sh) 仍可作为兼容旧习惯的薄包装使用。

眼镜端本地构建配置源放在业务工程：

```bash
cp host/glass/config/local_build.env.example host/glass/config/local_build.env
openaiglass.config.sync --app-root openaiglass-for-blind
```
