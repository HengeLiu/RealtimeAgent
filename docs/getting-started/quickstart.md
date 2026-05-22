# 快速开始

本页帮助你在本地跑通 `realtime-agent` 的最小链路：安装 SDK，启动示例 server，
连接开发/测试支持组件，并查看调试接口。

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

如果 `uv run realtime-agent.*` 找不到命令，重新执行 editable 安装：

```bash
uv pip install -e .
```

## 启动示例 server

```bash
uv run realtime-agent.server.run --app-name for-blind-app
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

## 打开浏览器眼镜模拟组件

在另一个终端运行：

```bash
uv run realtime-agent.web.open --serve
```

`--serve` 会启动本地静态服务并打开 browser-glass 页面，默认地址类似：

```text
http://127.0.0.1:8766/examples/dev-support/devices/browser-glass/index.html?server_url=http%3A%2F%2F127.0.0.1%3A8765
```

browser-glass 是开发支持组件，页面通过 ES module 导入本地 TypeScript Device SDK。
本地 HTTP origin 会保留浏览器对麦克风、摄像头和文件选择的授权缓存。

它在协议层注册为普通 Device，用于快速验证：

1. 设备注册。
2. 浏览器麦克风输入。
3. 浏览器摄像头输入。
4. server 下发 speaker stream。
5. 控制事件和 stream 生命周期。

## 可选：启动 Python 手机模拟组件

如果要联调当前找物 / 红绿灯视频任务，启动 Python phone preview。它是开发支持组件，
在协议层注册为普通 Device，不代表 SDK 内置了固定的手机设备类型：

```bash
uv run --extra gui python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

该命令会打开 PySide6 视频窗口，并注册为 `dev-python-phone-preview`。它声明
`endpoint.role.visual_display`、`endpoint.compute.vision`、`actuator.display.rgb`
和 `peer.video.receiver`，默认使用 `vision.provider=yolo`，最近原始帧写入
`runs/realtime-agent/python-phone/latest-rgb.png`，YOLO 标注帧写入
`runs/realtime-agent/python-phone/latest-yolo.jpg`。

如果只想验证简单设备协议、RGB 上传或振动 mock，可以另开 mock 配置。这个配置
同样属于开发/测试支持：

```bash
uv run python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

联调视频任务时，保持浏览器设备和 Python phone 使用同一个
`user_id`。当前 browser-glass 的“带图输入”只负责选择图片或视频资源；普通语音期间
server 的 realtime visual sampler 会按需请求单帧资产，但这类带 `request_id` 的
资产采样流只进入模型/资产链路，不会转发到 phone 窗口。只有普通连续 RGB stream，
或找物 / 红绿灯 Task 启动后建立的 `peer.video` 视频任务流，才会在 phone 窗口回显。

## 校验设备能力文件

```bash
uv run realtime-agent.device.validate examples/dev-support/devices/browser-glass/device.realtime-agent.yaml
```

设备能力文件描述端侧支持哪些传感器和执行器。业务 Tool / Task 会通过 Context API 使用这些能力。

## 跑一个无头回放测试

```bash
uv run python -m pytest examples/for-blind-app/replay-tests/test_vision_route_audio_samples.py -q
```

这条链路使用录制音频样例和 mock ASR，覆盖：

```text
sensor.mic -> ASR -> VisionRealtimeAgentCore -> Tool -> Streaming TTS -> actuator.speaker
```

## 看运行产物

默认运行产物写到应用目录下的 `runs/`。排查时优先看：

1. `model-request.json`
2. `agent-events.jsonl`
3. `tool-events.jsonl`
4. `events.jsonl`
5. `system-events.jsonl`

详细说明见 [runs 目录产物说明](../../agent-server/docs/how-to/inspect-runs-artifacts.md)。

## 下一步

- 想写业务能力，读 [第一个 Tool 和 Task](../tutorials/build-first-capability.md)。
- 想接入端侧设备，读 [设备能力与 Context API 开发说明](../../agent-server/docs/how-to/device-capability-development.md)。
- 想理解命令行入口，读 [CLI 参考](../reference/cli.md)。
- 想写独立端侧通讯代码，读 `devices/<language>/README.md`。
