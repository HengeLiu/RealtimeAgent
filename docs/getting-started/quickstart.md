# 快速开始

本页帮助你在本地跑通 `audio-chat` 的最小链路：安装 SDK，启动示例 server，连接浏览器参考设备，并查看调试接口。

## 环境要求

- macOS 或 Linux。
- Python 3.11。
- `uv`。
- 一个现代浏览器。

准备环境：

```bash
uv sync --python 3.11
uv pip install -e .
```

如果 `uv run audio-chat.*` 找不到命令，重新执行 editable 安装：

```bash
uv pip install -e .
```

## 启动示例 server

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

默认 server 地址：

```text
http://127.0.0.1:8765
```

常用健康检查：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

## 打开浏览器参考设备

在另一个终端运行：

```bash
uv run audio-chat.web.open --print-url
```

浏览器参考设备用于快速验证：

1. 设备注册。
2. 浏览器麦克风输入。
3. 浏览器摄像头输入。
4. server 下发 speaker stream。
5. 控制事件和 stream 生命周期。

## 可选：启动 Python phone mock

同一 `user_id` 下可以同时连接多个参考设备：

```bash
uv run python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

如果要查看 RGB stream 回显：

```bash
uv run --extra gui python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

该命令会打开 PySide6 视频窗口。保持浏览器设备和 Python phone 使用同一个
`user_id`，在 browser-glass 的“带图输入”区域点击“上传所选图片”，即可把图片通过
server 回显到 phone 窗口。

## 校验设备能力文件

```bash
uv run audio-chat.device.validate examples/dev-support/devices/browser-glass/device.audio-chat.yaml
```

设备能力文件描述端侧支持哪些传感器和执行器。业务 Tool / Task 会通过 Context API 使用这些能力。

## 跑一个无头回放测试

```bash
uv run python -m pytest examples/for-blind-app/tests/test_text_route_audio_samples.py -q
```

这条链路使用录制音频样例和 mock ASR，覆盖：

```text
sensor.mic -> ASR -> TextAgentCore -> Tool -> Streaming TTS -> actuator.speaker
```

## 看运行产物

默认运行产物写到应用目录下的 `runs/`。排查时优先看：

1. `model-request.json`
2. `agent-events.jsonl`
3. `tool-events.jsonl`
4. `events.jsonl`
5. `system-events.jsonl`

详细说明见 [runs 目录产物说明](../../audio-server/docs/how-to/inspect-runs-artifacts.md)。

## 下一步

- 想写业务能力，读 [第一个 Tool 和 Task](../tutorials/build-first-capability.md)。
- 想接入端侧设备，读 [设备能力与 Context API 开发说明](../../audio-server/docs/how-to/device-capability-development.md)。
- 想理解命令行入口，读 [CLI 参考](../reference/cli.md)。
