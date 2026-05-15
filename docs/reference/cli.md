# CLI 参考

`audio-chat` 提供一组命令用于启动 server、校验设备、打开开发/测试支持组件、
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
uv run audio-chat.server.run --app-name for-blind-app
```

按配置启动：

```bash
uv run audio-chat.server.run --config examples/for-blind-app/audio-server/server.yaml
```

## 设备能力

校验设备能力文件：

```bash
uv run audio-chat.device.validate examples/dev-support/devices/browser-glass/device.audio-chat.yaml
```

输出 JSON：

```bash
uv run audio-chat.device.validate examples/dev-support/devices/browser-glass/device.audio-chat.yaml --json
```

## 浏览器眼镜模拟组件

打开 browser-glass 开发支持组件：

```bash
uv run audio-chat.web.open --serve
```

`browser-glass` 是以 Device 形态运行的浏览器眼镜模拟组件，用于本地联调和手动测试，
不是 SDK 定义的正式设备类型。它使用 ES module 导入本地 TypeScript Device SDK，Chrome 不能在
`file://` 页面中加载这类本地模块。`--serve` 会用标准库启动一个轻量本地静态服务，
并打开 `http://127.0.0.1:8766/examples/dev-support/devices/browser-glass/index.html`。
端口默认固定为 `8766`，这样浏览器的 `localStorage`、IndexedDB 和样例目录授权可以
在下次启动后继续复用；如果端口被占用，可显式传 `--port`，但换端口会形成新的浏览器
origin，需要重新授权一次。
脚本检查时可使用：

```bash
uv run audio-chat.web.open --serve --print-url
```

## Python 开发支持组件

Python phone preview，用于当前视频回显、peer video 和 YOLO/YOLOE 视觉任务联调：

```bash
uv run --extra gui python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

Python phone mock，用于简单协议、RGB 上传和振动 mock 验证：

```bash
uv run python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

Python glass playback，人工播放参考组件：

```bash
uv run audio-chat.playback.glass --config examples/dev-support/devices/python-glass/playback.yaml
```

Python playback glass 系统回放端：

```bash
uv run audio-chat.playback-glass.run --help
```

## iOS 参考端

打开 iOS 参考端：

```bash
uv run audio-chat.ios.open
```

构建 iOS simulator：

```bash
uv run audio-chat.ios.build-sim
```

## ESP32-S3 参考端

生成本地配置：

```bash
uv run audio-chat.esp32.config
```

无硬件检查：

```bash
uv run audio-chat.esp32.build --dry-run
```

有 ESP-IDF 和硬件时：

```bash
uv run audio-chat.esp32.build
uv run audio-chat.esp32.flash --port /dev/tty.usbmodemXXXX
uv run audio-chat.esp32.monitor --port /dev/tty.usbmodemXXXX
```

## 开发检查

预检：

```bash
uv run audio-chat.dev.preflight --config examples/for-blind-app/audio-server/server.yaml
```

发布包检查：

```bash
uv run audio-chat.sdk.package-check --report runs/default-app/package-check.json
```

协议黄金样例和 Python 端侧 SDK 检查：

```bash
uv run python -m pytest audio-server/tests/test_protocol_schema_examples.py audio-server/tests/test_stream_chunk_codec_contract.py -q
uv run python -m pytest audio-device/python/tests -q
cd audio-device/typescript && npm test
cd audio-device/swift && swift test
cd audio-device/kotlin && gradle test
cmake -S audio-device/c -B audio-device/c/build
cmake --build audio-device/c/build
ctest --test-dir audio-device/c/build --output-on-failure
```

无头回放测试：

```bash
uv run python -m pytest examples/for-blind-app/tests/test_text_route_audio_samples.py -q
```

全部测试：

```bash
uv run python -m pytest
```
