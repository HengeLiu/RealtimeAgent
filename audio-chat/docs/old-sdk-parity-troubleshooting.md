# audio-chat 老 SDK 对齐阶段排障指南

更新时间：2026-05-07

本文面向从旧 `openaiglass-sdk` 迁移到 `audio-chat` 的功能开发者。排障时优先看结构化产物和验收报告，不要只看控制台日志。

## 1. 设备未注册

现象：

- `/api/debug/devices` 里没有目标设备。
- `runs/audio-chat/.../events.jsonl` 没有 `control.device.registered`。

检查：

1. `server_url`、`user_id`、`device_id`、token 是否来自同一次 `audio-chat.config.sync`。
2. 端侧是否连接到当前 server 端口。
3. auth 模式是否要求 token，token 是否过期或缺失。

命令：

```bash
uv run audio-chat.dev.preflight --config audio-chat/examples/minimal/server.yaml \
  --report audio-chat/runs/preflight.json
curl http://127.0.0.1:8765/api/debug/devices
```

## 2. 订阅未匹配

现象：

- Tool / Task 发布事件后端侧没有响应。
- `events.jsonl` 里事件存在，但匹配设备为空。

检查：

1. 端侧注册的 `subscriptions` 是否覆盖事件名，例如 `stream.control.*`。
2. 事件是否带了正确 `stream_type`。
3. Tool / Task 的 `require_capability` 是否和端侧 `capabilities` 一致。
4. server 配置是否允许通配订阅或精确订阅。

修复：

- 不要硬编码 `device_id`。
- 用 capability 和 subscription 修正匹配条件。
- 小 payload 只放语义和配置，大字节继续走 stream。

## 3. Stream 未打开

现象：

- 端侧说已收到配置事件，但没有 `stream.opened`。
- `stream-events.jsonl` 没有目标 `sensor.*` 或 `actuator.*`。

检查：

1. `stream.control.configure.requested` 的 `stream_type` 是否正确。
2. 端侧是否声明 `streams.produce` 或 `streams.consume`。
3. chunk 编码是否符合 `StreamFormat`。
4. chunk 大小是否超过 `stream_max_chunk_bytes`。

修复：

- 图片用 `sensor.rgb`。
- 麦克风用 `sensor.mic`。
- 播放用 `actuator.speaker`。
- 其他执行器按 `actuator.*` 扩展，不新增隐藏 RPC。

## 4. 没有资产

现象：

- `request_asset("sensor.rgb", ...)` 返回 `None`。
- `assets.jsonl` 没有新资产。

检查：

1. 端侧是否实际上传了 `sensor.rgb` stream chunk。
2. `freshness_seconds` 是否过小导致缓存未命中。
3. `timeout_seconds` 是否短于端侧采集和上传耗时。
4. `correlation_id` 是否和 Task watch 条件一致。

修复：

- 先用 `examples/basic-app/capabilities/capture_photo/tool.py` 验证单帧资产链路。
- 再用 `continuous_rgb_analyze` 验证连续资产消费。

## 5. Tool 未注册

现象：

- Agent 或测试报 unknown tool。
- `tool-events.jsonl` 没有目标 tool。

检查：

1. 业务 Tool 是否继承 `audio_chat.BaseTool`。
2. 是否只从 `audio_chat` 顶层导入公开 API。
3. `server.yaml` 中 `tools.discover.enabled`、`packages`、`recursive` 是否正确。
4. Tool 名称是否重复。

命令：

```bash
cd audio-chat
PYTHONPATH=examples/basic-app uv run audio-chat.server.run \
  --config examples/basic-app/config/server.yaml
```

## 6. Task 未恢复

现象：

- server 重启后长任务丢失。
- `task-events.jsonl` 有事件，但 TaskEngine 没恢复运行中任务。

检查：

1. Task 是否继承 `audio_chat.BaseTask`。
2. `tasks.discover` 是否能发现任务类。
3. `tasks.store` 是否配置了持久化路径。
4. 任务是否已经进入 `completed`、`cancelled`、`failed` 或 `timeout` 终态。

修复：

- Task 状态只通过 TaskEngine 流转。
- 任务外部事件通过 `TaskEventBridge` 回流。
- 不在业务代码里自建线程和私有任务表。

## 7. Output 被仲裁丢弃

现象：

- Tool / Task 调用了 `submit_text()`，但端侧没有听到。
- `output-decisions.jsonl` 显示 blocked、interrupted 或 dropped。

检查：

1. 当前是否已有更高优先级播放。
2. `priority`、`ttl_seconds` 是否符合业务场景。
3. 端侧是否声明 `streams.consume=["actuator.speaker"]`。
4. speaker output stream 是否成功打开。

修复：

- 紧急安全提示使用更高 priority。
- 普通状态更新允许排队或合并。
- 不直接绕过 Output Service 写播放器。

## 8. Provider Fallback

现象：

- 本地可跑，但没有调用真实 ASR / LLM / TTS provider。
- 集成测试被 skip 或报告 fallback。

检查：

1. `DASHSCOPE_API_KEY` 或其他 provider key 是否存在。
2. `allow_mock_fallback` 是否为 true。
3. preflight 的 `provider_keys` 检查结果。
4. 集成测试是否明确 skip，而不是假成功。

命令：

```bash
DASHSCOPE_API_KEY=... uv run python -m pytest audio-chat/tests/integration/test_dashscope_providers.py -q
```

## 9. iOS / ESP32 配置不一致

现象：

- 参考端能打开，但无法注册或连错 server。
- 真机和 playback 的 `user_id`、`device_id` 不一致。

检查：

1. 是否在同一网络下。
2. `server_url` 是否指向 Mac 当前局域网地址，而不是旧 IP。
3. iOS `AppConfig.example.json` 与 server YAML 是否来自同次同步。
4. ESP32 `local.env.example` 的 WiFi、server URL 和 token 是否同步。

修复：

```bash
uv run audio-chat.config.sync --app-root audio-chat/examples/basic-app
```

然后重新打开 iOS 参考端或重新配置 ESP32-S3 参考端。真机命令缺本地依赖时必须结构化失败，不能当作联调通过。
