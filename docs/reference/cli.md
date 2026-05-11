# CLI 参考

`audio-chat` 提供一组命令用于启动 server、校验设备、打开参考端、同步配置、做 package 检查和运行端侧辅助任务。

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
uv run audio-chat.server.run --config app-examples/for-blind-app/server.yaml
```

## 设备能力

校验设备能力文件：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml
```

输出 JSON：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml --json
```

## 浏览器参考设备

打开浏览器参考端：

```bash
uv run audio-chat.web.open --print-url
```

## Python 参考设备

Python phone mock：

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.mock.yaml
```

Python phone RGB 预览：

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.preview.yaml
```

Python glass playback：

```bash
uv run audio-chat.playback.glass --config app-examples/for-blind-app/host/glass-playback/sdk-playback.yaml
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
uv run audio-chat.esp32.build --dry-run --build-only
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
uv run audio-chat.dev.preflight --config app-examples/for-blind-app/server.yaml
```

发布包检查：

```bash
uv run audio-chat.sdk.package-check --report runs/default-app/package-check.json
```

无头回放测试：

```bash
uv run python -m pytest tests/test_text_route_audio_samples.py -q
```

全部测试：

```bash
uv run python -m pytest tests -q
```

