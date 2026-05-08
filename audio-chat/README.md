# audio-chat

`audio-chat` 是新的 server-side Python SDK，用于构建基于语音、事件和 stream 的多设备 AI 应用。它不是旧 `openaiglass-sdk` 的小修版本，而是把开发者入口收敛到 Python server SDK、`user_id` 下的 active device set、协议事件、stream、Tool / Task、Memory / Skill / MCP、Output Service、播放仲裁和设备级回放。

业务代码只依赖 `audio_chat` 顶层公开 API。设备侧通过 subscriptions 声明自己要接收哪些事件，并通过 stream 上传或消费数据；server 不负责录音、播放、唤醒词、端侧 AEC 或硬件驱动。

## 1. 功能扩展开发

功能扩展开发者编写的是 server 侧业务能力，不直接实现设备连接，也不面向某个 `device_id` 编程。能力通过 SDK 提供的 Context 表达业务意图，由 server 根据设备注册时提交的 `subscriptions` 完成路由。

功能扩展开发者需要关注这几件事：

- 你要实现什么能力：继承 `BaseTool` 或 `BaseTask`。
- 你需要什么输入：用 `input_model` 或 `ToolSpec` 告诉模型参数结构。
- 你如何访问设备：通过 `context.devices`，也就是 `UserDeviceContext`。
- 你如何协调设备：发送事件、配置 `sensor.*` stream、打开 `actuator.*` stream。
- 你如何读取设备数据：读取 SDK 从 `sensor.*` stream 整理出的 `AssetRef`。
- 你如何输出给用户：调用 Output Service，或写入 `actuator.*` stream。

Tool 用于短动作能力，例如请求一张图片、查一次搜索、准备一段路线：

```python
from audio_chat import BaseTool, ToolContext, ToolResult


class CapturePhotoTool(BaseTool):
    name = "capture_photo"
    description = "请求当前用户设备上传一张前方图片。"
    input_model = dict

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        asset = context.devices.request_asset(
            "sensor.rgb",
            freshness_seconds=0,
            timeout_seconds=3,
        )
        if asset is None:
            return ToolResult.failed("拍照超时")
        return ToolResult.ok({"asset_id": asset.asset_id, "mime_type": asset.mime_type})
```

Task 用于长流程能力，例如连续视觉分析、导航执行期、计时器和后台通知：

```python
from audio_chat import BaseTask, TaskContext, TaskEvent


class ContinuousVisionTask(BaseTask):
    task_type = "continuous_vision"

    async def on_start(self, context: TaskContext) -> None:
        context.devices.configure_stream(
            "sensor.rgb",
            mode="continuous",
            rate_hz=1,
        )

    async def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        await context.emit_progress("视觉任务正在运行")
```

`UserDeviceContext` 是 Tool / Task 访问当前用户设备集合的唯一入口：

- `get_devices()`：查询只读设备快照，用于状态说明、日志和调试展示。
- `publish_event(...)`：发布协议事件，由订阅匹配分发。
- `configure_stream(...)`：请求订阅命中的设备打开、调整或停止 `sensor.*` stream。
- `request_asset(...)`：请求某类 `sensor.*` stream 的最新结果，例如一张 `sensor.rgb` 图片。
- `query_assets(...)` / `watch_assets(...)`：读取 server 内部缓存的 stream 结果。
- `open_output_stream(...)`：打开 `actuator.*` 输出 stream。
- `submit_text(...)` / `submit_audio(...)`：进入 Output Service 和播放仲裁。

这里的 `AssetRef` 是能力开发者使用的 server 内部缓存引用，不是设备协议对象。设备开发者只上传 stream；能力开发者可以通过 `AssetRef` 读取或传递这些 stream 结果。

最小样板见：

- `examples/basic-app/capabilities/sample_tool/tool.py`
- `examples/basic-app/capabilities/capture_photo/tool.py`
- `examples/basic-app/capabilities/sample_task/task.py`
- `examples/basic-app/capabilities/continuous_rgb_analyze/task.py`
- `examples/migration-templates`

关键约束：

- Tool / Task 只能通过 `context.devices` 使用设备通讯能力。
- 不硬编码 `device_id` 做点对点发送。
- 图片、音频、视频和文件不能放进控制事件 payload，必须走 `sensor.*` / `actuator.*` stream。
- Memory / Skill / MCP 不能直接持有设备上下文；需要设备通讯能力时，封装成 Tool 或 Task。

## 2. 设备开发

`Device` 是接入 audio-chat server 的任意端侧实例。它可以运行在浏览器、Python 脚本、iOS App、ESP32 固件、Android App、Linux 盒子或其他环境中。SDK 不规定设备类型，也不要求开发者把自己的设备代码放进 `audio-chat` 仓库。

设备开发者只需要关注两件事：

- 设备身份：`device_id` / `user_id` / `name`。其中 `name` 只用于日志、debug API 和人工观察，不参与路由决策。
- 设备订阅和属性：用 `subscriptions` 声明要接收哪些事件，也就是设备能参与哪些交互；用可选 `properties` 描述调试信息或硬件参数。例如订阅后上传 `sensor.mic`、`sensor.rgb`、`sensor.imu`，或接收 `actuator.speaker`、`actuator.haptic`。

最小注册事件示例：

```json
{
  "event_name": "control.device.register.requested",
  "user_id": "user-demo-001",
  "producer_id": "dev-browser-001",
  "payload": {
    "device_id": "dev-browser-001",
    "name": "浏览器调试设备",
    "client_type": "browser",
    "subscriptions": [
      {"event": "control.audio_session.*"},
      {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
      {"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}
    ],
    "properties": {
      "audio.aec": "browser_webrtc",
      "audio.input.sample_rate": 16000,
      "camera.facing": "front"
    }
  }
}
```

通讯方式只有两类：

- 事件：用于注册、心跳、会话打开关闭、stream 控制、设备命令和状态通知。
- stream：用于传输音频、图片、视频、IMU、深度图、播放器输出、震动输出等连续或大字节数据。

设备侧不需要理解 Tool、Task、Agent Core 或 Asset Service。设备只要按注册时声明的 `subscriptions` 消费事件，并按事件要求生产或消费 stream 即可。

事件分发规则是固定的：

1. server 只在同一个 `user_id` 当前在线设备中查找订阅者。
2. 默认不把事件回发给事件生产者自己。
3. 先匹配订阅的 `event`，再匹配 `filter`。
4. 对 `sensor.*` 和 `actuator.*`，只要事件名和 `stream_type` filter 命中就发送。
5. Tool / Task 可以指定 `selection="first_available"` 或 `selection="all"`，但不能指定某个 `device_id`。

`filter` 只过滤事件字段，例如 `stream_type`、`payload.command_name`、`payload.mode`。`properties` 不参与事件路由，只用于日志、debug、硬件参数说明或业务偏好。

本仓库的 `endpoints-examples` 只提供两个目的：

- 帮助 SDK 开发者验证 server 协议、回放和自动验收。
- 给设备开发者提供浏览器、Python、iOS、ESP32-S3 等设备实现案例。

这些示例不是协议约束。真实设备如何实现、运行在哪个仓库、使用哪种语言、是否同时具备感知、执行或端侧算力能力，都由设备开发者决定。

设备示例的后续设计见：

- [Browser Device 设计文档](docs/browser-device-design.md)
- [Python Device Sim 设计文档](docs/python-device-sim-design.md)
- [事件订阅与分发优化说明](docs/event-subscription-routing-optimization.md)

## 3. 安装

在仓库根目录执行：

```bash
uv sync --python 3.11
uv pip install -e audio-chat
```

如果 `uv run audio-chat.*` 找不到命令，重新执行 editable 安装。

当前可试用发布候选版本为 `0.1.0rc1`。发布前检查流程：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py old-sdk-parity-release \
  --report runs/acceptance/old-sdk-parity-release.json
uv run audio-chat.sdk.package-check \
  --report runs/acceptance/old-sdk-parity-package-check.json
```

## 4. 同步配置

最小样例：

```bash
uv run audio-chat.config.sync --app-root audio-chat/examples/basic-app
```

同步后重点确认：

- `examples/basic-app/config/server.yaml`
- `examples/basic-app/host/phone-mock/config.yaml`
- `examples/basic-app/host/glass-playback/playback.yaml`
- `device-examples/browser-device/browser-device.yaml`
- `endpoints-examples/ios-phone/AppConfig.example.json`
- `endpoints-examples/esp32-s3/local.env.example`

本阶段配置同步以开发样例为主；真机侧正式打开、构建、烧录命令由 `old-sdk-parity-cli` 线路继续补齐。

## 5. 启动 Server

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

## 6. 启动设备模拟器和参考设备

Python 设备模拟器：

```bash
uv run audio-chat.phone.mock --config audio-chat/endpoints-examples/python-phone-mock/phone.mock.yaml
```

设备回放：

```bash
uv run audio-chat.playback.glass --config audio-chat/examples/minimal/playback.yaml
```

使用已录制 WAV 作为模拟设备的麦克风输入：

```bash
uv run audio-chat.playback.glass \
  --server-url http://127.0.0.1:8765 \
  --audio-wav openaiglass-sdk/testdata/audio-sample/wav/看一下我前面有什么.wav
```

浏览器设备示例：

```bash
uv run audio-chat.web.open --print-url
```

iOS 设备示例入口：

```bash
open audio-chat/endpoints-examples/ios-phone
```

ESP32-S3 设备示例入口：

```bash
open audio-chat/endpoints-examples/esp32-s3
```

iOS / ESP32-S3 目录目前是设备示例和契约入口。缺少 Xcode、ESP-IDF、串口或真实设备时，真机步骤只能作为待验证流程；真机 smoke 由 `old-sdk-parity-phone` 和 `old-sdk-parity-esp32` 线路补齐。

## 7. Memory / Skill / MCP

Memory、Skill、MCP 已作为 Agent Core 能力面接入。业务侧规则是：

- Memory 用来注入模型上下文和提供 `memory_search` / `manage_memory` 类能力。
- Skill 用来声明受控能力说明、工具白名单和会话状态。
- MCP 用来接地图、搜索、业务系统等外部方法。
- Memory / Skill / MCP 不能直接持有设备上下文；需要设备通讯能力时，封装成 Tool 或 Task，再通过 `UserDeviceContext` 调用。

迁移说明见 `docs/phase3-migration-guide.md`。

## 8. 跑回放和验收

开发者最小闭环：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py developer-usability \
  --report runs/acceptance/developer-usability.json
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
uv run python scripts/acceptance_check.py old-sdk-parity-capabilities \
  --report runs/acceptance/old-sdk-parity-capabilities.json
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

## 9. 看日志产物

优先看结构化产物，而不是只看控制台日志：

- `runs/audio-chat/.../events.jsonl`：设备注册、订阅匹配、控制事件。
- `runs/audio-chat/.../stream-events.jsonl`：stream 打开、chunk、关闭。
- `runs/audio-chat/.../assets.jsonl`：server 内部缓存的 `sensor.*` stream 结果引用。
- `runs/audio-chat/.../tool-events.jsonl`：Tool 入参、结果和错误。
- `runs/audio-chat/.../task-events.jsonl`：Task 生命周期和通知决策。
- `runs/audio-chat/.../output-decisions.jsonl`：Output Service 和播放仲裁结果。
- `runs/audio-chat/.../result.json`：回放最终摘要。

排障入口见 `docs/old-sdk-parity-troubleshooting.md`。

## 10. 老 SDK 迁移入口

老业务能力迁移优先从这些位置开始：

- `docs/phase3-migration-guide.md`
- `examples/migration-templates`
- `examples/for-blind-app`：已经包含 find_object、traffic_light、navigation、search、timer 的可执行迁移样板。
- `docs/old-sdk-parity-development-plan.md`

老 SDK 到 `audio-chat` 的主要入口映射：

| 老 SDK 入口 | audio-chat 当前入口 | 说明 |
| --- | --- | --- |
| `openaiglass.config.sync` | `audio-chat.config.sync` | 同步 server、mock、playback、web、参考端配置。 |
| `openaiglass.server.run` | `audio-chat.server.run` | 通过 YAML 和 app-root 启动 server。 |
| `openaiglass.phone.mock` | `audio-chat.phone.mock` | Python phone mock 参考端。 |
| `openaiglass.glass.start --runtime playback` | `audio-chat.playback.glass` | 设备级回放入口。 |
| `openaiglass.phone.open` | `endpoints-examples/ios-phone` | 当前为 iOS 参考端目录，CLI 由后续线路补齐。 |
| `openaiglass.glass.start` | `endpoints-examples/esp32-s3` | 当前为 ESP32-S3 参考端目录，构建烧录由后续线路补齐。 |
| `openaiglass.sdk.preflight` | `audio-chat.dev.preflight` | 本地配置、provider、设备示例和 package 预检。 |
| `BaseTool` / `BaseTask` | `audio_chat.BaseTool` / `audio_chat.BaseTask` | 顶层公开 API。 |
| `DeviceGroupContext` | `audio_chat.UserDeviceContext` | 只按 user active device set、event subscription 和 stream 工作。 |

## 11. 当前状态口径

文档中写“已实现”的能力必须有代码、测试、样板或验收 lane 支撑。没有自动验收的内容只能写成参考端、迁移目标、后续线路或真机 smoke 待补齐。
