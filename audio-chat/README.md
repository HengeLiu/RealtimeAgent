# audio-chat

`audio-chat` 是新的 server-side Python SDK，用于构建基于语音、事件和 stream 的多设备 AI 应用。它不是旧 `openaiglass-sdk` 的小修版本，而是把开发者入口收敛到 Python server SDK、`user_id` 下的 active device set、协议事件、stream、资产缓存、Tool / Task、Memory / Skill / MCP、Output Service、播放仲裁和设备级回放。

业务代码只依赖 `audio_chat` 顶层公开 API。设备侧通过 capabilities 和 subscriptions 声明自己能生产或消费哪些 stream、能响应哪些事件；server 不负责录音、播放、唤醒词、端侧 AEC 或硬件驱动。

## 1. 安装

在仓库根目录执行：

```bash
uv sync --python 3.11
uv pip install -e audio-chat
```

如果 `uv run audio-chat.*` 找不到命令，重新执行 editable 安装。

## 2. 同步配置

最小样例：

```bash
uv run audio-chat.config.sync --app-root audio-chat/examples/basic-app
```

同步后重点确认：

- `examples/basic-app/config/server.yaml`
- `examples/basic-app/host/phone-mock/config.yaml`
- `examples/basic-app/host/glass-playback/playback.yaml`
- `endpoints/web-glass/web-glass.yaml`
- `endpoints/ios-phone/AppConfig.example.json`
- `endpoints/esp32-s3/local.env.example`

本阶段配置同步以开发样例为主；真机侧正式打开、构建、烧录命令由 `old-sdk-parity-cli` 线路继续补齐。

## 3. 启动 Server

最小 server：

```bash
uv run audio-chat.server.run --config audio-chat/examples/minimal/server.yaml
```

业务样例 server：

```bash
cd audio-chat
PYTHONPATH=examples/basic-app uv run audio-chat.server.run \
  --config examples/basic-app/config/server.yaml
```

后台开发流程可以先 dry-run 检查命令参数：

```bash
uv run audio-chat.server.start --config audio-chat/examples/minimal/server.yaml --dry-run
uv run audio-chat.server.logs --log-file audio-chat/runs/audio-chat/server.log
uv run audio-chat.server.stop --dry-run
```

常用调试接口：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/users/user-playback-001
curl http://127.0.0.1:8765/api/debug/playback
```

## 4. 启动端侧 Mock 和参考端

Python phone mock：

```bash
uv run audio-chat.phone.mock --config audio-chat/endpoints/python-phone-mock/phone.mock.yaml
```

Glass playback：

```bash
uv run audio-chat.playback.glass --config audio-chat/examples/minimal/playback.yaml
```

Web glass：

```bash
uv run audio-chat.web.open --print-url
```

iOS 参考端入口：

```bash
open audio-chat/endpoints/ios-phone
```

ESP32-S3 参考端入口：

```bash
open audio-chat/endpoints/esp32-s3
```

iOS / ESP32 目录目前是参考端和契约入口。缺少 Xcode、ESP-IDF、串口或真实设备时，真机步骤只能作为待验证流程；真机 smoke 由 `old-sdk-parity-phone` 和 `old-sdk-parity-esp32` 线路补齐。

## 5. 写 Tool

Tool 用于短动作能力，例如请求一张图片、查一次搜索、准备一段路线。业务代码只从 `audio_chat` 顶层导入：

```python
from audio_chat import BaseTool, ToolContext, ToolResult
```

最小样板见：

- `examples/basic-app/capabilities/sample_tool/tool.py`
- `examples/basic-app/capabilities/capture_photo/tool.py`
- `examples/migration-templates/find_object/tool.py`

关键约束：

- Tool 只能通过 `context.devices` 使用设备能力。
- 不硬编码 `device_id` 做点对点发送。
- 图片、音频、视频和文件不能放进控制事件 payload，必须走 `sensor.*` / `actuator.*` stream 或资产缓存。

## 6. 写 Task

Task 用于长流程能力，例如连续视觉分析、导航执行期、计时器和后台通知。业务代码只从 `audio_chat` 顶层导入：

```python
from audio_chat import BaseTask, TaskContext, TaskEvent
```

最小样板见：

- `examples/basic-app/capabilities/sample_task/task.py`
- `examples/basic-app/capabilities/continuous_rgb_analyze/task.py`
- `examples/basic-app/capabilities/timer/task.py`
- `examples/migration-templates/continuous_rgb_analyze/task.py`
- `examples/migration-templates/notification_task/task.py`

Task 不直接操作播放队列或设备连接；状态变化通过 `TaskEvent` 回流，用户可听见的输出进入 Output Service。

## 7. 使用 UserDeviceContext

`UserDeviceContext` 替代旧 SDK 文档中的 `DeviceGroupContext`，是 Tool / Task 访问用户设备集合的唯一入口：

```python
from audio_chat import UserDeviceContext
```

当前开发者常用方法：

- `get_devices(capability=...)`：查询只读设备快照。
- `find_device(capability=...)`：按能力查找只读设备句柄。
- `publish_event(...)`：发布协议事件，由订阅匹配分发。
- `request_asset(...)`：请求 `sensor.*` 资产，例如一张 `sensor.rgb` 图片。
- `query_assets(...)` / `watch_assets(...)`：读取资产缓存或持续消费资产。
- `open_output_stream(...)`：打开 `actuator.*` 输出 stream。
- `submit_text(...)` / `submit_audio(...)`：进入 Output Service 和播放仲裁。

## 8. Memory / Skill / MCP

Memory、Skill、MCP 已作为 Agent Core 能力面接入。业务侧规则是：

- Memory 用来注入模型上下文和提供 `memory_search` / `manage_memory` 类能力。
- Skill 用来声明受控能力说明、工具白名单和会话状态。
- MCP 用来接地图、搜索、业务系统等外部方法。
- Memory / Skill / MCP 不能直接持有设备上下文；需要设备能力时，封装成 Tool 或 Task，再通过 `UserDeviceContext` 调用。

迁移说明见 `docs/phase3-migration-guide.md`。

## 9. 跑回放和验收

开发者最小闭环：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py developer-usability \
  --report runs/acceptance/developer-usability.json
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
uv run python scripts/acceptance_check.py old-sdk-parity-docs \
  --report runs/acceptance/old-sdk-parity-docs.json
```

当前全量基线：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py all --keep-going \
  --report runs/acceptance/old-sdk-parity-full.json
```

真实 provider smoke 与本地 mock 回放分开运行；缺少 key 时应跳过或结构化失败，不能伪装成真实 provider 成功：

```bash
uv run python -m pytest audio-chat/tests/integration/test_dashscope_providers.py -q
```

## 10. 看日志产物

优先看结构化产物，而不是只看控制台日志：

- `runs/audio-chat/.../events.jsonl`：设备注册、订阅匹配、控制事件。
- `runs/audio-chat/.../stream-events.jsonl`：stream 打开、chunk、关闭。
- `runs/audio-chat/.../assets.jsonl`：`sensor.rgb` 等资产引用。
- `runs/audio-chat/.../tool-events.jsonl`：Tool 入参、结果和错误。
- `runs/audio-chat/.../task-events.jsonl`：Task 生命周期和通知决策。
- `runs/audio-chat/.../output-decisions.jsonl`：Output Service 和播放仲裁结果。
- `runs/audio-chat/.../result.json`：回放最终摘要。

排障入口见 `docs/old-sdk-parity-troubleshooting.md`。

## 11. 老 SDK 迁移入口

老业务能力迁移优先从这些位置开始：

- `docs/phase3-migration-guide.md`
- `examples/migration-templates`
- `examples/for-blind-app`
- `docs/old-sdk-parity-development-plan.md`

老 SDK 到 `audio-chat` 的主要入口映射：

| 老 SDK 入口 | audio-chat 当前入口 | 说明 |
| --- | --- | --- |
| `openaiglass.config.sync` | `audio-chat.config.sync` | 同步 server、mock、playback、web、参考端配置。 |
| `openaiglass.server.run` | `audio-chat.server.run` | 通过 YAML 和 app-root 启动 server。 |
| `openaiglass.phone.mock` | `audio-chat.phone.mock` | Python phone mock 参考端。 |
| `openaiglass.glass.start --runtime playback` | `audio-chat.playback.glass` | 设备级回放入口。 |
| `openaiglass.phone.open` | `endpoints/ios-phone` | 当前为 iOS 参考端目录，CLI 由后续线路补齐。 |
| `openaiglass.glass.start` | `endpoints/esp32-s3` | 当前为 ESP32-S3 参考端目录，构建烧录由后续线路补齐。 |
| `openaiglass.sdk.preflight` | `audio-chat.dev.preflight` | 本地配置、provider、endpoint 和 package 预检。 |
| `BaseTool` / `BaseTask` | `audio_chat.BaseTool` / `audio_chat.BaseTask` | 顶层公开 API。 |
| `DeviceGroupContext` | `audio_chat.UserDeviceContext` | 只按 user active device set、capability 和 subscription 工作。 |

## 12. 当前状态口径

文档中写“已实现”的能力必须有代码、测试、样板或验收 lane 支撑。没有自动验收的内容只能写成参考端、迁移目标、后续线路或真机 smoke 待补齐。
