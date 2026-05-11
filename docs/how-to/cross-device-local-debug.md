# 跨设备本地联调

多设备应用的排障重点不是单个进程能否启动，而是确认 server、设备注册、事件路由、stream、资产、Tool、Task 和输出播放是否形成闭环。

本页给出推荐联调顺序。

## 1. 准备环境

```bash
uv sync --python 3.11
uv pip install -e .
```

建议本地开发使用 DEBUG 日志。示例应用的 `server.yaml` 默认已经使用：

```yaml
server:
  log_level: "DEBUG"
```

## 2. 校验设备能力文件

先校验浏览器参考设备：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml
```

如果要联调其他设备，也先检查对应配置文件。

## 3. 启动 server

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

确认 server 健康：

```bash
curl http://127.0.0.1:8765/api/health
```

## 4. 连接浏览器参考设备

```bash
uv run audio-chat.web.open --print-url
```

在浏览器页面中完成设备注册，然后检查：

```bash
curl http://127.0.0.1:8765/api/debug/devices
```

需要确认：

1. 设备在线。
2. `user_id` 符合预期。
3. `device_id` 唯一。
4. 能力声明包含当前要测试的传感器或执行器。

## 5. 可选：连接 Python phone mock

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.mock.yaml
```

如果要查看 RGB stream：

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.preview.yaml
```

## 6. 可选：运行 iOS 参考端

```bash
uv run audio-chat.ios.open
```

iOS 参考端适合验证手机端注册、日志、直连相机接收和 Swift 端协议实现。真机联调时需要同时看 iOS app 日志和 server runs 产物。

## 7. 可选：运行 ESP32-S3 参考端检查

无硬件时先做 dry-run：

```bash
uv run audio-chat.esp32.config
uv run audio-chat.esp32.build --dry-run --build-only
```

有 ESP-IDF 和真机时再做 build、flash、monitor。

## 8. 看调试接口

```bash
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

如果设备没有响应，先看设备是否在线，再看事件路由。

## 9. 看 runs 产物

排查顺序：

1. `events.jsonl`：设备注册、唤醒、音频 session、stream 请求。
2. `stream-events.jsonl`：stream 是否打开、是否有 chunk。
3. `agent-events.jsonl`：模型链路是否进入 Agent Core。
4. `tool-events.jsonl`：Tool 是否被调用、参数和结果是什么。
5. `model-request.json`：模型最终看到了什么 prompt、messages 和 tools。
6. `system-events.jsonl`：系统级异常和 provider 降级。

详细文件说明见 [runs 目录产物说明](inspect-runs-artifacts.md)。

## 常见判断

设备没出现在 debug API：

- 优先检查 server 地址、WebSocket URL、device token、`user_id`。

Tool 没有调用：

- 看 `model-request.json` 中是否包含该 Tool schema。
- 看 `agent-events.jsonl` 是否有模型响应和 tool call。

抓拍没有图片：

- 看设备能力是否声明 RGB。
- 看 `events.jsonl` 是否有 `stream.control.open.requested`。
- 看 `stream-events.jsonl` 是否有输入 stream。
- 看 `assets.jsonl` 和 `photos/` 是否有资产。

有音频但没播放：

- 看 `output-decisions.jsonl` 或 `playback-decisions.jsonl`。
- 看 `/api/debug/playback`。
- 看端侧是否消费 `actuator.speaker` stream。

