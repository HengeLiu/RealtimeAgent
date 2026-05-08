# audio-chat

`audio-chat` 是面向语音交互、多设备协作和实时 stream 的 server-side Python SDK。当前仓库已经升级为以新 SDK 为主的组织方式：

- `audio-chat-sdk/`：Python server SDK 源码，发布包名为 `audio-chat`，导入名为 `audio_chat`。
- `app-examples/`：基于 SDK 的应用样例，新的业务应用从这里开始。
- `device-examples/`：浏览器、Python、iOS、ESP32-S3 等参考端侧实现。端侧实现已经从 SDK 子目录中独立出来，体现 SDK 只承担 server-side 职责。
- `docs/`：架构设计、迁移说明、联调和排障文档。
- `testdata/`、`tests/`、`scripts/`：契约样例、自动化测试和验收脚本。
- `legacy/`：旧 `openaiglass-sdk`、旧 `openaiglass-for-blind`、旧根目录文档和历史运行资产。它们只作为迁移参考，不再作为新开发入口。

server 不负责录音、播放、唤醒词、端侧 AEC 或硬件驱动。设备通过注册事件声明 `user_id`、`device_id`、订阅策略和属性；业务 Tool / Task 通过 `UserDeviceContext` 表达设备通讯意图；server 根据事件订阅和 stream 元数据完成路由。

## 快速开始

准备环境：

```bash
uv sync --python 3.11
uv pip install -e .
```

如果 `uv run audio-chat.*` 找不到命令，重新执行 editable 安装。

启动基础应用 server：

```bash
uv run audio-chat.server.run --app-name basic-app
```

启动盲人眼镜业务样例 server：

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

常用调试接口：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

## 启动参考设备

浏览器设备：

```bash
uv run audio-chat.web.open --print-url
```

Python phone mock：

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.mock.yaml
```

Python 手机视频显示端：

```bash
uv run python -m audio_chat_python_phone_mock --config device-examples/python-phone/phone.preview.yaml
```

该端侧会打开 OpenCV 视频窗口，注册到 server，并订阅同一 `user_id` 下的
`sensor.rgb` 输入流。眼镜端或浏览器端上传 RGB stream 后，画面会回显到这个窗口，
最近一帧会写入 `runs/audio-chat/python-phone/latest-rgb.jpg`。

Python glass playback：

```bash
uv run audio-chat.playback.glass --config app-examples/basic-app/host/glass-playback/sdk-playback.yaml
```

使用录制音频驱动 playback：

```bash
uv run audio-chat.playback.glass \
  --server-url http://127.0.0.1:8765 \
  --audio-wav legacy/openaiglass-sdk/testdata/audio-sample/wav/看一下我前面有什么.wav
```

Text 模型路线的无头验收可以直接复用旧 SDK 的 AudioSample。mock ASR 会把 WAV 文件名作为转写文本，mock text model 会按文本意图触发真实 ToolGateway，因此这条链路能覆盖 `sensor.mic -> ASR -> TextAgentCore -> Tool -> Streaming TTS -> actuator.speaker`：

```bash
uv run python -m pytest tests/test_text_route_audio_samples.py -q
```

iOS 参考端：

```bash
uv run audio-chat.ios.open
```

ESP32-S3 参考端：

```bash
uv run audio-chat.esp32.config
uv run audio-chat.esp32.build --dry-run --build-only
```

## 开发者工作模型

`audio-chat` 的业务开发不是直接操作 WebSocket，也不是在代码里写死某一台设备。开发者需要理解四个稳定概念：

| 概念 | 谁来实现 | 作用 |
| --- | --- | --- |
| 设备 | 端侧开发者 | 注册到某个 `user_id`，声明自己订阅哪些事件、具备哪些属性，并负责麦克风、摄像头、播放器、振动器等硬件。 |
| 事件 | 设备和 server 都会产生 | 小体积控制信令，例如注册、唤醒、打开音频会话、请求相机上传、请求振动、任务状态变化。 |
| stream | 设备和 server 都会读写 | 大字节或连续数据，例如 `sensor.mic`、`sensor.rgb`、`sensor.imu`、`actuator.speaker`。 |
| Tool / Task | 应用开发者 | Tool 做一次性能力，Task 做长流程能力；它们通过 `context.devices` 发布事件、请求 stream 资产、提交输出。 |

一次完整链路通常是：

```text
设备注册并提交订阅
  -> 用户语音唤醒并上传 sensor.mic stream
  -> Agent Core 理解用户意图
  -> 模型调用 Tool 或启动 Task
  -> Tool / Task 通过 context.devices 发布控制事件或请求资产
  -> Control Service 按订阅策略选择在线设备
  -> 设备收到事件后执行硬件动作，并按需上传 sensor.* stream
  -> Asset Service 缓存图片、IMU、深度图等资产
  -> Tool / Task 读取资产或收到事件，返回结果或继续运行
  -> Agent / Task 输出进入 Output Service
  -> Output Service 经过播放仲裁后下发 actuator.speaker stream
```

这套模型的关键点是：业务代码面向“当前用户有哪些在线设备能响应这个事件或 stream 类型”，而不是面向“某个固定 device_id”。`device_id` 只用于注册、日志、调试快照和端侧回执，不能作为业务逻辑里的固定目标。

### 设备如何接入能力

端侧先写一份设备能力文件，例如 [device-examples/browser-glass/device.audio-chat.yaml](device-examples/browser-glass/device.audio-chat.yaml)。这份文件只描述“我支持哪些传感器和执行器”，不要求端侧开发者手写事件订阅。

内置语义 ID 先固定为六类：

| 语义 ID | 含义 | 常用参数 |
| --- | --- | --- |
| `sensor.rgb` | RGB 相机。 | `modes`、`formats`、`width`、`height`、`frequency_hz`、`sample_count`、`duration_seconds` |
| `sensor.imu` | IMU 传感器。 | `modes`、`frequency_hz`、`sample_count`、`duration_seconds`、`options.axes` |
| `sensor.mic` | 麦克风。 | `sample_rate_hz`、`channels`、`frequency_hz`、`codecs` |
| `sensor.tof` | ToF 深度相机。 | `modes`、`formats`、`width`、`height`、`frequency_hz` |
| `actuator.speaker` | 扬声器。 | `codecs`、`sample_rates_hz`、`channels` |
| `actuator.haptic` | 振动器。 | `commands`、`options` |

示例：

```yaml
$schema: ../../spec/audio-chat-device.schema.json

device_id: dev-browser-glass-001
user_id: user-browser-glass-001
name: 浏览器调试设备

supports:
  - id: sensor.mic
    sample_rate_hz: 48000
    channels: 1
    frequency_hz: 50

  - id: sensor.rgb
    modes: [single, continuous]
    formats: [jpeg]
    frequency_hz: 1
    width: 1280
    height: 720

  - id: sensor.imu
    modes: [continuous]
    frequency_hz: 30

  - id: sensor.tof
    modes: [single, continuous]
    frequency_hz: 5

  - id: actuator.speaker
    codecs: [pcm16le]
    sample_rates_hz: [16000, 24000]

  - id: actuator.haptic
    commands: [vibrate]
```

`$schema` 指向 [spec/audio-chat-device.schema.json](spec/audio-chat-device.schema.json)，编辑器可以据此提示字段和可选值。本地校验命令：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml
```

校验通过后，SDK 会把 `supports` 编译成注册事件里的 `subscriptions`。例如 `sensor.rgb` 会编译成：

```json
{"event": "stream.control.*", "filter": {"stream_type": "sensor.rgb"}}
```

Tool / Task 发布 RGB 采集请求时，Control Service 根据这条订阅找到浏览器设备并下发事件。端侧收到的仍然是标准协议事件，例如 `stream.control.configure.requested + stream_type=sensor.rgb + payload.mode=single`。

### Tool 如何使用设备

Tool 适合“用户问一句，系统完成一次动作”的场景，例如抓拍、搜索、查询设备、准备路线。Tool 的输入参数通过 Pydantic 模型定义，SDK 会把 schema 暴露给大模型。

Tool 与设备交互有三种常见方式：

| 场景 | 推荐方法 | 数据如何流动 |
| --- | --- | --- |
| 控制一个小动作 | `context.devices.publish_event(...)` | payload 里放小体积控制参数，例如振动模式、导航目的地、本地模式切换。 |
| 获取一张图片或一次传感器数据 | `context.devices.request_asset(...)` | Tool 发布 stream 控制事件，设备打开 `sensor.*` stream 上传，server 缓存为 AssetRef。 |
| 给用户播报结果 | `context.devices.submit_text(...)` 或返回 ToolResult 交给 Agent | 文本进入 Output Service，经 TTS 和播放仲裁后下发 `actuator.speaker`。 |

示例：请求具备 RGB 能力的设备上传一张当前图片。这里不把“拍照”建模成设备方法，Tool 只表达“需要一个 `sensor.rgb` 资产”；具体是浏览器相机、手机相机、眼镜相机，还是未来其他视觉传感器，由订阅命中的端侧自己处理。

```python
from pydantic import BaseModel, Field

from audio_chat import BaseTool, ErrorCode, StreamType, ToolContext, ToolError, ToolResult, ToolSpec


class CurrentImageInput(BaseModel):
    reason: str = Field(default="agent_requested", description="请求端侧上传当前图片的原因。")
    timeout_seconds: float = Field(default=10, gt=0, description="等待 sensor.rgb 资产返回的超时时间。")


class GetCurrentImageTool(BaseTool):
    spec = ToolSpec(
        name="get_current_image",
        description="当用户需要了解眼前画面时，请求当前用户设备上传一张 sensor.rgb 图片资产。",
        input_model=CurrentImageInput,
        progress_message="我先获取一张当前画面。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        asset = context.devices.request_asset(
            StreamType.SENSOR_RGB,
            freshness_seconds=0,
            configure_payload={
                "reason": str(input_data.get("reason") or "agent_requested"),
                "mode": "single",
                "format": "jpeg",
            },
            timeout_seconds=float(input_data.get("timeout_seconds") or 10),
        )
        if asset is None:
            return ToolResult.failed(ToolError("获取当前图片超时", code=ErrorCode.TIMEOUT))
        return ToolResult.success({"asset_id": asset.asset_id, "path": asset.path}, assets=[asset])
```

示例：给端侧发送一个不需要 stream 的执行器控制事件。

```python
from audio_chat import BaseTool, EventName, ToolContext, ToolResult, ToolSpec


class VibrateTool(BaseTool):
    spec = ToolSpec(
        name="vibrate",
        description="请求当前用户的在线端侧设备执行一次短振动提醒。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        result = context.devices.publish_event(
            EventName.CONTROL_DEVICE_COMMAND_REQUESTED,
            payload={"command": "haptic.vibrate", "pattern": "short"},
            selection="first_available",
        )
        return ToolResult.success({"delivered_count": result.delivered_count})
```

### Task 如何串起复杂流程

Task 适合“启动后持续一段时间”的能力，例如连续视觉分析、导航执行期、计时器、后台提醒。Task 不代表端侧也有一个同名任务；对端侧来说，它只是在收到控制事件后打开或关闭某些 stream，或者上报事件。

Task 常见模式：

1. `on_start()` 发布控制事件，要求设备启动某种连续 stream。
2. 设备按订阅收到事件，打开摄像头、IMU、深度相机等传感器。
3. 设备持续上传 `sensor.*` stream，server 把每帧或每段缓存成资产。
4. Task 用 `watch_assets()` 逐个读取资产，做分析、调用模型或更新状态。
5. Task 需要提示用户时调用 `context.devices.submit_text()`，不要直接写播放器。
6. Task 结束或取消时发布 `mode=stop` 控制事件，要求端侧关闭对应 stream。

如果用户说“开始帮我持续看前方”或“边走边观察红绿灯”，推荐做法是 Tool 只负责启动一个视频/连续视觉 Task，真正的连续帧处理放在 Task 里。

```python
from pydantic import BaseModel, Field

from audio_chat import BaseTool, ErrorCode, ToolContext, ToolError, ToolResult, ToolSpec


class StartVideoInput(BaseModel):
    fps: float = Field(default=1, gt=0, le=10, description="期望端侧上传 sensor.rgb 的频率。")
    frame_limit: int = Field(default=10, ge=1, le=300, description="本轮最多处理多少帧。")


class StartVideoStreamTool(BaseTool):
    spec = ToolSpec(
        name="start_video_stream_analyze",
        description="当用户需要持续观察画面、持续找物或连续识别红绿灯时，启动 sensor.rgb 连续分析任务。",
        input_model=StartVideoInput,
        progress_message="我开始持续观察画面。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        if context.tasks is None:
            return ToolResult.failed(
                ToolError("当前应用没有启用 Task Engine", code=ErrorCode.PROVIDER_UNAVAILABLE)
            )

        ref = await context.tasks.create(
            task_type="video_stream_analyze",
            user_id=context.user_id,
            session_id=context.session_id,
            input_data=input_data,
            summary="连续 RGB 视频流分析",
        )
        return ToolResult.success({"task_id": ref.task_id, "state": ref.state})
```

```python
from audio_chat import BaseTask, StreamType, TaskContext


class VideoStreamAnalyzeTask(BaseTask):
    task_type = "video_stream_analyze"
    description = "请求端侧持续上传 sensor.rgb，并逐帧读取资产进行分析。"

    async def on_start(self, context: TaskContext) -> None:
        input_data = dict(context.metadata.get("input") or {})
        correlation_id = context.task_ref.task_id

        context.devices.configure_stream(
            StreamType.SENSOR_RGB,
            mode="continuous",
            rate_hz=float(input_data.get("fps") or 1),
            payload={
                "reason": "video_stream_analyze",
                "format": "jpeg",
                "asset_policy": "cache",
                "correlation_id": correlation_id,
            },
        )

        frame_limit = int(input_data.get("frame_limit") or 10)
        frame_count = 0
        async for asset in context.devices.watch_assets(
            StreamType.SENSOR_RGB,
            correlation_id=correlation_id,
            timeout_seconds=30,
        ):
            frame_count += 1
            # 这里可以调用视觉模型、规则判断或业务服务处理当前帧。
            if frame_count >= frame_limit:
                break

        await context.complete(
            {"frame_count": frame_count},
            summary=f"已处理 {frame_count} 帧 sensor.rgb 画面",
        )

    async def on_cancel(self, context: TaskContext) -> None:
        context.devices.configure_stream(StreamType.SENSOR_RGB, mode="stop")
```

如果只是让设备执行一个小动作，例如震动一次、切换本地模式、返回一个小状态摘要，不需要建立 stream，直接发控制事件即可。只有图片、音频、视频、IMU 窗口、深度图这类大字节或连续数据才走 stream。

## 应用开发

功能扩展开发者只依赖 `audio_chat` 顶层公开 API，不直接 import SDK 内部服务对象，也不硬编码 `device_id` 点对点发送事件。

默认应用目录结构如下：

```text
app-examples/<your-app>/
  server.yaml
  capabilities/
    your_tool/
      tool.py      # 继承 BaseTool，会被自动发现
    your_task/
      task.py      # 继承 BaseTask，会被自动发现
  skills/
  config/
```

`server.yaml` 中配置自动发现包后，开发者只需要把 `BaseTool` / `BaseTask` 子类放进对应 package。SDK 启动时会扫描这些类，把 Tool schema 注册给 Agent Core，把 Task 类型注册给 Task Engine。业务代码不需要在 `app.py` 里手写注册逻辑。

新增一个能力时，推荐按这个顺序设计：

1. 先判断能力是一次性动作还是长流程：一次性动作写 Tool，长流程写 Task。
2. 列出它需要哪些端侧能力：例如 `sensor.rgb`、`sensor.imu`、`actuator.speaker`、`haptic.vibrate`。
3. 确认端侧设备能力文件中已经声明对应 `supports`。
4. 在 Tool / Task 中通过 `context.devices` 发布控制事件或请求资产。
5. 用 `runs/audio-chat/...` 中的 `events.jsonl`、`stream-events.jsonl`、`tool-events.jsonl`、`task-events.jsonl` 验证链路。

业务样例：

- `app-examples/basic-app`：最小 Tool / Task / playback 样板。
- `app-examples/for-blind-app`：盲人眼镜业务样例，包含找物、红绿灯、导航、搜索和计时器迁移版本。
- `app-examples/for-blind-app/templates`：新能力开发模板。

关键约束：

- Tool / Task 只能通过 `context.devices` 访问设备通讯能力。
- 不直接把图片、音频、视频或文件字节放进控制事件 payload，大字节数据必须走 `sensor.*` / `actuator.*` stream。
- Memory / Skill / MCP 不直接持有设备上下文；需要设备通讯能力时封装成 Tool 或 Task。
- 设备开发者只实现事件和 stream 协议，不需要理解 Agent Core、Tool、Task 或 Asset Service。

## 设备开发

端侧开发者优先维护设备能力文件，不直接维护 subscriptions。浏览器示例：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml --json
```

命令会输出可直接用于注册事件 payload 的 `supports` 和编译后的 `subscriptions`。server 只在同一个 `user_id` 当前在线设备中投递事件；设备接到事件后按 `event_name + stream_type + payload.mode` 路由到对应硬件实现。

端侧实现入口：

- `device-examples/browser-glass`
- `device-examples/python-glass`
- `device-examples/python-phone`
- `device-examples/native-ios-phone`
- `device-examples/native-esp32-glass`

## 配置同步与检查

生成本地联调配置：

```bash
uv run audio-chat.config.sync --app-root app-examples/basic-app
```

预检：

```bash
uv run audio-chat.dev.preflight --config app-examples/basic-app/server.yaml
```

发布包检查：

```bash
uv run audio-chat.sdk.package-check --report runs/audio-chat/package-check.json
```

自动验收：

```bash
uv run python scripts/acceptance_check.py old-sdk-parity-release \
  --report runs/acceptance/old-sdk-parity-release.json
```

## 测试

```bash
uv run python -m pytest tests -q
```

真实 provider 集成测试需要配置对应 API Key：

```bash
uv run python -m pytest tests/integration/test_dashscope_providers.py -q
```

当前代码仍可能有开发中测试失败；目录升级不隐藏这些问题，后续修复应继续在新的根目录结构下进行。

## 文档

- [SDK 架构设计](docs/audio-chat-sdk-architecture.md)
- [迁移指南](docs/phase3-migration-guide.md)
- [运行产物说明](docs/runs-artifacts-guide.md)
- [浏览器设备设计](docs/browser-glass-design.md)
- [ESP32-S3 参考端说明](docs/esp32-s3-endpoint-bridge.md)
- [历史 SDK 可用性对齐计划](docs/old-sdk-parity-development-plan.md)

历史资料在 `legacy/` 下保留。需要查旧实现时优先看 `legacy/openaiglass-sdk` 和 `legacy/openaiglass-for-blind`，新开发不要再从这些目录复制入口命令或导入路径。
