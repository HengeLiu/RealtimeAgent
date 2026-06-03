# realtime-agent examples

`examples/device_app_demo` 是当前推荐的最小端侧 App 验证入口。它面向 Device SDK 和真实设备联调，使用独立 server 配置验证设备注册、控制事件、音频上行、相机帧上传和 speaker 下行播放链路。

`examples/dev-support` 提供浏览器和 Python 端侧开发支持组件，用于本地联调、协议验证和回放测试。历史业务示例如果仍在仓库中，只作为参考材料，不再作为 README 推荐入口。

| 目录 | 当前定位 |
| --- | --- |
| `device_app_demo` | 当前推荐的 Device SDK 验证示例。包含专用 `agent-server/server.yaml`、Web Chat、Swift Device Demo App 和 ESP32-S3 固件参考实现。 |
| `dev-support` | 浏览器眼镜模拟、Python 手机模拟、Python playback glass 等开发/测试支持组件与契约测试辅助实现。它们以 Device 形态接入协议，但不是 SDK 预设的正式设备类型。 |

推荐启动方式：

```bash
# 在项目根目录执行
uv run realtime-agent.server.run --config examples/device_app_demo/agent-server/server.yaml
```

`device_app_demo` 的配置只用于验证端侧 SDK 链路，不加载历史业务应用能力。浏览器联调时可以打开 Web Chat：

```bash
uv run realtime-agent.web-chat.open
```

iOS 真机联调时可以打开 Swift demo：

```bash
uv run realtime-agent.ios.open
```

ESP32-S3 真机联调前，需要先配置 Wi-Fi、server 地址和板级引脚，再在固件目录构建：

```bash
cd examples/device_app_demo/esp32-s3/firmware
idf.py set-target esp32s3
idf.py build
```

推荐验收：

```bash
# 在项目根目录执行
uv run realtime-agent.dev.preflight \
  --config examples/device_app_demo/agent-server/server.yaml \
  --report runs/acceptance/preflight.json

uv run python -m pytest \
  examples/device_app_demo/app-tests \
  examples/dev-support/unit-tests \
  examples/dev-support/app-tests \
  -q
```
