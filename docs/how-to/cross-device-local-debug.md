# 跨设备本地联调

多设备应用的排障重点不是单个进程能否启动，而是确认 server、设备注册、事件路由、stream、资产、Tool、Task 和输出播放是否形成闭环。

本页给出当前推荐的本地联调顺序。默认 server 使用 `examples/device_demo/agent-server/server.yaml`，用于验证 Device SDK 注册、音频上行、相机帧上传、speaker 下行播放和控制事件。

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

先校验浏览器眼镜模拟组件的能力文件：

```bash
uv run realtime-agent.device.validate examples/dev-support/devices/browser-glass/device.realtime-agent.yaml
```

如果要联调其他设备，也先检查对应配置文件。

## 3. 启动 server

```bash
uv run realtime-agent.server.run --config examples/device_demo/agent-server/server.yaml
```

确认 server 健康：

```bash
curl http://127.0.0.1:8765/api/health
```

## 4. 连接浏览器眼镜模拟组件

```bash
uv run realtime-agent.web.open --serve
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

说明：browser-glass 是开发/测试支持组件。它会像真实端侧一样注册成 Device，
从而覆盖控制事件、stream 和输出播放链路；但它不是 RealtimeAgent SDK 的正式设备类型。

## 5. 可选：连接 Python 手机模拟组件

Python phone preview 可用于视频回显、peer video 和端侧视觉调试：

```bash
uv run --extra gui python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

如果只验证简单协议、RGB 上传或振动 mock，再使用 mock 配置：

```bash
uv run python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

Python phone 同样是开发/测试支持组件。它在协议层注册为普通 Device，用来模拟手机侧
显示、视觉计算和 peer video receiver，不代表 SDK 内置固定手机类型。

preview 配置通过 properties 声明 `device_role=phone`、`endpoint.role.visual_display`、
`endpoint.compute.vision`、`actuator.display.rgb` 和 `peer.video.receiver`。它作为
显示与视觉计算组件运行，RGB 画面来自 browser-glass 或 peer video sender。业务任务是否会启动 peer video，取决于当前 server 配置中是否加载了对应 Tool / Task。

观察点：

1. Python phone 窗口状态栏的 `registered=true` 和 `frame` 计数。
2. `runs/realtime-agent/python-phone/latest-rgb.png` 是否更新。
3. `runs/realtime-agent/python-phone/latest-yolo.jpg` 是否显示 YOLO 标注框。
4. server `command-events.jsonl` 中是否按顺序出现 `peer.video.receiver.start` 和
   `peer.video.sender.start`。
5. server `stream-events.jsonl` 中带 `request_id` 的资产采样流不应包含 phone consumer。

## 6. 可选：运行 Swift Device Demo

```bash
uv run realtime-agent.ios.open
```

Swift Device Demo 适合验证真机注册、日志、麦克风上行、相机帧上传、speaker 下行播放和 Swift Device SDK 协议实现。真机联调时需要同时看 iOS app 日志和 server runs 产物。

## 7. 可选：运行 ESP32-S3 参考端检查

无硬件时先做 dry-run：

```bash
uv run realtime-agent.esp32.config
uv run realtime-agent.esp32.build --dry-run
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

详细文件说明见 [runs 目录产物说明](../../agent-server/docs/how-to/运行产物排查说明.md)。

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
