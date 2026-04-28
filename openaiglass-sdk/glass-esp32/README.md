# glass-esp32

本目录放 ESP32 通用眼镜 SDK 运行时。它负责 WiFi、控制连接、音频、摄像头、端侧命令处理和 SDK 协议适配，不放 `find_object`、导航或其他盲人产品业务策略。

当前仍保留为 ESP-IDF 可构建工程，`sdk-v13` 起额外提供 `component-manifest.json` 作为源码包清单。清单声明当前可发布输入，包括 ESP-IDF 工程文件、main 组件文件、托管依赖和公开能力；`openaiglass.sdk.package-check` 会检查这些文件是否齐全。

```bash
openaiglass glass firmware --build-only --repo-root .
```

当前包形态是 `esp-idf-source-project`，用于内部源码集成和版本检查，不是发布到 ESP-IDF component registry 的独立组件。后续如果要成为正式组件，应先拆分 `main/glass_main.c` 中的运行时边界，再把宿主工程配置、硬件型号和业务差异留在业务侧。

盲人产品的眼镜宿主配置和硬件说明位于 [../../openaiglass-for-blind/host/glass](../../openaiglass-for-blind/host/glass)。
