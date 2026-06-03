# CLI 参考

`realtime-agent` 提供一组命令用于启动 server、校验设备、打开开发/测试支持组件、
同步配置、做 package 检查和运行端侧辅助任务。

所有命令建议通过 `uv run` 执行。

## 安装

```bash
uv sync --python 3.11
uv pip install -e .
```

## Server

启动示例应用：

```bash
uv run realtime-agent.server.run --config examples/device_app_demo/agent-server/server.yaml
```

其他应用也推荐按配置启动：

```bash
uv run realtime-agent.server.run --config examples/<your-app>/agent-server/server.yaml
```

## 设备能力

校验设备能力文件：

```bash
uv run realtime-agent.device.validate examples/dev-support/devices/browser-glass/device.realtime-agent.yaml
```

输出 JSON：

```bash
uv run realtime-agent.device.validate examples/dev-support/devices/browser-glass/device.realtime-agent.yaml --json
```

## Web Chat Device Demo

打开当前推荐的 Web Chat demo：

```bash
uv run realtime-agent.web-chat.open
```

该命令会用标准库启动仓库根目录静态服务，并打开
`http://127.0.0.1:8766/examples/device_app_demo/web-chat/`。
Web Chat 通过 JavaScript Device SDK 接入 server，适合验证浏览器麦克风、相机、speaker
和标准设备事件链路。脚本检查时可使用：

```bash
uv run realtime-agent.web-chat.open --print-url
```

## 浏览器眼镜模拟组件

打开 browser-glass 开发支持组件：

```bash
uv run realtime-agent.web.open --serve
```

`browser-glass` 是以 Device 形态运行的浏览器眼镜模拟组件，用于本地联调和手动测试，
不是 SDK 定义的正式设备类型。它使用本地浏览器端 Device 适配代码接入协议。`--serve`
会用标准库启动一个轻量本地静态服务，并打开
`http://127.0.0.1:8766/examples/dev-support/devices/browser-glass/index.html`。
端口默认固定为 `8766`，这样浏览器的 `localStorage`、IndexedDB 和样例目录授权可以
在下次启动后继续复用；如果端口被占用，可显式传 `--port`，但换端口会形成新的浏览器
origin，需要重新授权一次。
脚本检查时可使用：

```bash
uv run realtime-agent.web.open --serve --print-url
```

## Python 开发支持组件

Python phone preview，用于当前视频回显、peer video 和 YOLO/YOLOE 视觉任务联调：

```bash
uv run --extra gui python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

Python phone mock，用于简单协议、RGB 上传和振动 mock 验证：

```bash
uv run python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

Python playback glass 系统回放端：

```bash
uv run realtime-agent.playback-glass.run --help
```

## iOS SDK Demo

打开 Swift Device SDK Demo：

```bash
uv run realtime-agent.ios.open
```

构建 Swift Device SDK Demo：

```bash
uv run realtime-agent.ios.build-sim
```

## ESP32-S3 参考端

生成本地配置：

```bash
uv run realtime-agent.esp32.config
```

外部固件工程无硬件检查：

```bash
uv run realtime-agent.esp32.build --project-dir /path/to/esp32-firmware --dry-run
```

有 ESP-IDF 和硬件时：

```bash
uv run realtime-agent.esp32.build --project-dir /path/to/esp32-firmware
uv run realtime-agent.esp32.flash --project-dir /path/to/esp32-firmware --port /dev/tty.usbmodemXXXX
uv run realtime-agent.esp32.monitor --project-dir /path/to/esp32-firmware --port /dev/tty.usbmodemXXXX
```

## 开发检查

预检：

```bash
uv run realtime-agent.dev.preflight --config examples/device_app_demo/agent-server/server.yaml
```

发布包检查：

```bash
uv run realtime-agent.sdk.package-check --report runs/default-app/package-check.json
```

协议黄金样例和多语言 Device SDK 检查：

```bash
uv run python -m pytest protocol/protocol-tests/test_protocol_schema_examples.py protocol/protocol-tests/test_stream_chunk_codec_contract.py -q
cd devices/javascript && npm test
cd devices/swift && swift test
cmake -S devices/c -B /tmp/realtime-agent-device-c-build
cmake --build /tmp/realtime-agent-device-c-build
ctest --test-dir /tmp/realtime-agent-device-c-build --output-on-failure
```

Device Demo 契约测试：

```bash
uv run python -m pytest examples/device_app_demo/app-tests -q
```

全部测试：

```bash
uv run python -m pytest
```
