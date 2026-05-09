# audio-chat 设备注册与功能开发说明

本文面向当前版本 `audio-chat` 的功能开发者和设备开发者。它描述的是当前仓库已经可用的开发方式，不是阶段性计划文档。

当前代码已经提供 typed device facade，例如 `context.devices.sensors.rgb.one()`、`context.devices.commands.call()`、`context.output.say()` 和 `context.assets.get()`。设备注册同时兼容旧的 `supports[].id` 写法和新的 `supports.sensors[].type` / `supports.actuators[].type` 写法；部分长流和远程命令回报仍是兼容层实现。更完整的目标设计见 [Context 与设备 API 设计说明](context-device-api-design.md)。

## 1. 当前开发模型

当前版本已经形成一条可运行闭环：

```text
设备注册并声明 supports
  -> 用户通过端侧上传 sensor.mic 音频
  -> Agent Core 识别用户意图
  -> 模型调用 Tool 或启动 Task
  -> Tool / Task 通过 context.devices 使用设备能力
  -> 端侧按控制消息打开传感器或执行动作
  -> 大字节数据通过 stream 上传或下发
  -> server 生成 AssetRef 或输出语音
```

功能开发者主要写：

- `BaseTool`：一次性、短耗时能力。
- `BaseTask`：长流程、持续观察、后台状态机。

设备开发者主要写：

- 设备能力文件。
- 注册逻辑。
- 控制消息处理。
- stream 上传或播放逻辑。

## 2. 快速启动

安装 SDK：

```bash
uv sync --python 3.11
uv pip install -e .
```

校验浏览器设备能力文件：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml
```

启动示例应用：

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

如果使用后台 server 管理命令：

```bash
uv run audio-chat.server.start --app-name for-blind-app
uv run audio-chat.server.logs
uv run audio-chat.server.stop
```

打开浏览器设备：

```bash
uv run audio-chat.web.open --print-url
```

查看设备是否注册成功：

```bash
curl http://127.0.0.1:8765/api/debug/devices
```

## 3. 设备注册文件

设备能力文件推荐使用结构化 `supports`，也继续兼容旧的 `supports[].id` 列表。浏览器参考端仍保留旧写法，方便和现有端侧代码对照：

[device-examples/browser-glass/device.audio-chat.yaml](../device-examples/browser-glass/device.audio-chat.yaml)

核心结构：

```yaml
$schema: ../../spec/audio-chat-device.schema.json

device_id: dev-browser-glass-001
user_id: user-browser-glass-001
name: 浏览器调试设备
device_name: browser-glass
client_type: browser-glass
sdk_version: audio-chat-browser-glass-0.1.0

runtime:
  platform: browser
  language: javascript
  version: 0.1.0

supports:
  - id: sensor.mic
    modes: [continuous]
    sample_rate_hz: 48000
    channels: 1
    frequency_hz: 50
    codecs: [pcm16le]
    options:
      aec: browser_webrtc
      noise_suppression: browser_webrtc

  - id: sensor.rgb
    modes: [single, continuous]
    formats: [jpeg]
    frequency_hz: 1
    sample_count: 1
    width: 1280
    height: 720
    options:
      facing: environment

  - id: actuator.speaker
    codecs: [pcm16le]
    sample_rates_hz: [16000, 24000]
    channels: 1

properties:
  debug.manual_events: true
  debug.file_upload: true
```

字段说明：

| 字段 | 当前作用 |
| --- | --- |
| `device_id` | 设备实例 ID，用于注册、日志和调试。功能代码不要写死它。 |
| `user_id` | 设备绑定的用户。 |
| `device_name` / `client_type` | 设备实现名称，用于日志和调试。 |
| `device_role` | 设备在当前用户设备组中的角色，例如 `front_glass`、`phone`，用于 selector 选择设备。 |
| `tags` | 设备标签，例如 `primary`、`debug`，用于 selector 进一步约束。 |
| `runtime` | 端侧运行环境说明。 |
| `supports.sensors[].type` | 推荐写法，声明传感器能力，例如 `rgb`、`imu`、`tof`、`mic`。 |
| `supports.actuators[].type` | 推荐写法，声明执行器能力，例如 `speaker`、`vibrator`。 |
| `supports[].id` | 兼容写法，声明标准能力 ID，例如 `sensor.rgb`、`sensor.imu`、`actuator.haptic`。 |
| `default` | 推荐写法里的默认采样参数，例如 `fps`、`sample_rate_hz`、`duration_seconds`、`width`、`height`。 |
| `external` / `options` | 设备扩展字段，会随注册 payload 透传；新写法推荐使用 `external`，旧写法继续支持 `options`。 |
| `properties` | 调试或路由辅助属性。 |

推荐的新写法示例：

```yaml
$schema: ../../spec/audio-chat-device.schema.json

device_id: dev-browser-glass-001
user_id: user-browser-glass-001
device_name: browser-glass
device_role: front_glass
tags: [primary, debug]

runtime:
  platform: browser
  language: javascript
  version: 0.1.0

supports:
  sensors:
    - type: mic
      default:
        sample_rate_hz: 48000
        channels: 1
        frequency_hz: 50
        codecs: [pcm16le]
      external:
        aec: browser_webrtc
        noise_suppression: browser_webrtc

    - type: rgb
      modes: [single, continuous]
      default:
        fps: 1
        sample_count: 1
        width: 1280
        height: 720
        format: jpeg
      external:
        facing: environment

  actuators:
    - type: speaker
      default:
        codecs: [pcm16le]
        sample_rates_hz: [16000, 24000]
        channels: 1
    - type: vibrator
      external:
        default_pattern: short
```

`audio-chat.device.validate` 会把上述结构编译成 server 注册时使用的 `supports[].id` 和 `subscriptions`，设备代码不需要手写底层订阅。

当前内置能力：

| 能力 | 说明 |
| --- | --- |
| `sensor.mic` | 麦克风，属于系统音频输入链路。 |
| `sensor.rgb` | RGB 相机，支持单帧和连续上传。 |
| `sensor.imu` | IMU。 |
| `sensor.tof` | ToF 深度相机。 |
| `actuator.speaker` | 扬声器，属于系统音频输出链路。 |
| `actuator.haptic` | 振动器。 |

校验命令：

```bash
uv run audio-chat.device.validate device-examples/browser-glass/device.audio-chat.yaml --json
```

校验输出会包含注册 payload 和编译后的订阅策略。端侧可以直接使用这些内容完成注册。

## 4. 设备端需要实现什么

设备端至少要实现：

1. 连接 server 控制 WebSocket。
2. 发送注册请求，提交 `user_id`、`device_id`、`supports`、`properties`。
3. 保持心跳。
4. 处理 server 下发的控制消息。
5. 对传感器能力打开 stream 并上传数据。
6. 对执行器能力接收 stream 或执行控制动作。

浏览器参考端可直接查看：

- [device-examples/browser-glass/index.html](../device-examples/browser-glass/index.html)
- [device-examples/browser-glass/device.audio-chat.yaml](../device-examples/browser-glass/device.audio-chat.yaml)
- [device-examples/browser-glass/README.md](../device-examples/browser-glass/README.md)

设备端不要关心 server 上有哪些 Tool 或 Task。设备只需要遵守自己注册的能力语义，并在收到对应控制消息后执行硬件动作。

## 5. Tool 开发

Tool 适合一次性能力，例如：

- 获取当前画面。
- 查询在线设备。
- 执行一次搜索。
- 启动一个长任务。
- 发起一次端侧小动作。

Tool 继承 `BaseTool`，通过 `ToolSpec` 声明名称、描述和输入输出模型。输入模型推荐使用 Pydantic，字段描述会暴露给大模型。

最小 Tool：

```python
from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class EchoInput(BaseModel):
    """Echo Tool 输入参数。"""

    text: str = Field(description="要原样返回的文本。")


class EchoTool(BaseTool):
    """最小 Tool 样板。"""

    spec = ToolSpec(
        name="echo_text",
        description="原样返回输入文本，并附带当前用户在线设备数量。仅用于开发示例和联调。",
        input_model=EchoInput,
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        text = input_data["text"]
        return ToolResult.success(
            data={"text": text, "device_count": len(context.devices.get_devices())},
            message=text,
        )
```

参考实现：

[app-examples/for-blind-app/capabilities/sample_tool/tool.py](../app-examples/for-blind-app/capabilities/sample_tool/tool.py)

## 6. Tool 中获取一次传感器数据

当前版本推荐新 Tool 优先使用 typed facade 请求一次传感器数据。以 RGB 图片为例：

```python
asset = await context.devices.sensors.rgb.one(
    selector={"device_role": "front_glass"},
    timeout_seconds=10,
    params={
        "width": 1280,
        "height": 720,
        "format": "jpeg",
    },
)
```

兼容旧代码时，也可以继续用 `context.devices.request_asset()`：

```python
asset = context.devices.request_asset(
    "sensor.rgb",
    freshness_seconds=0,
    configure_payload={
        "mode": "single",
        "reason": "capture_current_view",
        "format": "jpeg",
    },
    timeout_seconds=10,
)
```

typed facade 返回 `AssetRef`，超时会抛出结构化异常；兼容 API 返回 `AssetRef | None`：

- 返回 `AssetRef`：server 收到端侧上传的数据，并生成引用。
- 返回 `None`：超时或没有设备响应。

完整示例：

```python
from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class CaptureInput(BaseModel):
    reason: str = Field(default="agent_requested", description="为什么需要获取当前画面。")
    timeout_seconds: float = Field(default=10, gt=0, description="等待图片返回的超时时间。")


class CaptureCurrentViewTool(BaseTool):
    spec = ToolSpec(
        name="capture_current_view",
        description="请求当前用户设备上传一张 RGB 图片。",
        input_model=CaptureInput,
        progress_message="我先看一下当前画面。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        asset = await context.devices.sensors.rgb.one(
            selector={"device_role": "front_glass"},
            timeout_seconds=float(input_data.get("timeout_seconds") or 10),
            params={
                "format": "jpeg",
                "reason": input_data.get("reason") or "agent_requested",
            },
        )
        return ToolResult.success(
            data={"captured": True, "asset_id": asset.asset_id, "path": asset.path},
            assets=[asset],
            message="已收到当前画面。",
        )
```

参考实现：

[app-examples/for-blind-app/capabilities/find_object/tool.py](../app-examples/for-blind-app/capabilities/find_object/tool.py)

## 7. Tool 中启动 Task

Tool 不应该自己维护长时间循环。需要持续观察、导航、计时器这类能力时，Tool 只负责创建 Task。

当前版本 `ToolContext` 中有 `context.tasks`，这是兼容实现。新设计会把启动 Task 收敛成专门 Tool，但当前代码可以这样写：

```python
ref = await context.tasks.create(
    task_type="find_object_vision_task",
    user_id=context.user_id,
    session_id=context.session_id,
    input_data=dict(input_data),
    summary="持续找物任务",
)
return ToolResult.success(
    data={"started": True, "task_id": ref.task_id, "state": ref.state},
    tasks=[ref],
    message="找物任务已启动",
)
```

开发约束：

- Tool 只启动任务，不在内部跑持续循环。
- Task 类型必须已经被自动发现注册。
- 如果 `context.tasks is None`，要返回清晰失败结果。

## 8. Task 开发

Task 适合长流程，例如：

- 连续 RGB 画面分析。
- 交通灯持续识别。
- 导航执行期状态维护。
- 计时器和后台提醒。
- 端侧远程任务状态跟踪。

当前 Task 继承 `BaseTask`，主要实现：

- `on_start(context)`
- `on_event(context, event)`
- `on_cancel(context)`

最小连续 RGB Task：

```python
from audio_chat import BaseTask, TaskContext


class ContinuousRgbAnalyzeTask(BaseTask):
    task_type = "continuous_rgb_analyze"
    description = "读取连续 RGB 资产并生成最小分析结果"

    async def on_start(self, context: TaskContext) -> None:
        input_data = dict(context.metadata.get("input") or {})
        frame_limit = int(input_data.get("frame_limit") or 2)
        correlation_id = str(input_data.get("correlation_id") or context.task_ref.task_id)

        context.devices.publish_event(
            "stream.control.configure.requested",
            stream_type="sensor.rgb",
            payload={
                "mode": "continuous",
                "fps": int(input_data.get("fps") or 2),
                "format": "jpeg",
                "asset_policy": "cache",
                "correlation_id": correlation_id,
            },
            selection="first_available",
        )

        frames = []
        async for asset in context.devices.watch_assets(
            "sensor.rgb",
            correlation_id=correlation_id,
            timeout_seconds=float(input_data.get("timeout_seconds") or 1),
        ):
            frames.append(asset)
            if len(frames) >= frame_limit:
                break

        context.devices.submit_text(f"已分析 {len(frames)} 帧画面", priority="normal")

    async def on_cancel(self, context: TaskContext) -> None:
        context.devices.publish_event(
            "stream.control.configure.requested",
            stream_type="sensor.rgb",
            payload={"mode": "stop", "reason": "task_cancelled"},
            selection="all",
        )
```

参考实现：

[app-examples/for-blind-app/capabilities/continuous_rgb_analyze/task.py](../app-examples/for-blind-app/capabilities/continuous_rgb_analyze/task.py)

## 9. 设备输出和语音提示

当前版本中，业务代码可以用：

```python
context.devices.submit_text("我正在处理", priority="low")
```

这会进入 Output Service，由 TTS、播放仲裁和端侧扬声器 stream 统一处理。

开发约束：

- 不直接写 `actuator.speaker` stream。
- 不在 Tool / Task 中自己做 TTS 播放。
- 模型自身的音频输出也会进入同一套播放链路。

## 10. 自动发现

业务应用不需要在 `app.py` 手写工具注册。把 `BaseTool` / `BaseTask` 子类放到配置指定的包下即可。

默认应用目录：

```text
app-examples/<your-app>/
  server.yaml
  capabilities/
    your_tool/
      tool.py
    your_task/
      task.py
```

`server.yaml` 中配置扫描包后，SDK 会自动发现 Tool 和 Task。

示例应用：

[app-examples/for-blind-app](../app-examples/for-blind-app/README.md)

## 11. 当前限制

当前版本仍处在新旧接口过渡期，有几个限制需要明确：

- `context.devices.sensors.rgb.one()`、`context.devices.commands.call()`、`context.output.say()` 已有兼容实现，但仍复用底层 `UserDeviceContext` 和旧协议。
- `DeviceContext` 当前是 `TaskContext` 的公开别名，Task 中长流能力通过 `allow_long_running=True` 的设备上下文启用。
- `selector` 已可用于 typed sensor API 的设备筛选；当前主要按 `device_role`、`device_name`、`tags` 和标准能力 ID 匹配。
- `supports.sensors[].type` / `supports.actuators[].type` 已可用于设备能力文件，旧的 `supports[].id` 仍保留兼容。
- 当前 `UserDeviceContext` 仍是功能代码访问设备的兼容入口。
- 当前 `selection="first_available"` / `selection="all"` 仍在使用，后续会被 selector 规则替换。

因此，当前新 Tool 可以优先试用 typed facade；新设备能力文件优先按结构化 `supports` 写，涉及持续流和远程任务时仍要参考当前兼容 API。

## 12. 调试和验收

常用调试接口：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

常用运行产物：

| 文件 | 用途 |
| --- | --- |
| `runs/audio-chat/<user_id>/<device_id>/events.jsonl` | 控制消息和模型过程事件。 |
| `runs/audio-chat/<user_id>/<device_id>/stream-events.jsonl` | stream 开启、关闭和摘要。 |
| `runs/audio-chat/<user_id>/<device_id>/assets.jsonl` | 资产写入和请求结果。 |
| `runs/audio-chat/<user_id>/<device_id>/tool-events.jsonl` | Tool 调用、参数、结果和错误。 |
| `runs/audio-chat/<user_id>/<device_id>/task-events.jsonl` | Task 生命周期。 |
| `runs/audio-chat/<user_id>/<device_id>/output-decisions.jsonl` | 播放仲裁和输出决策。 |

常用测试：

```bash
uv run python -m pytest tests/test_docs_commands.py -q
uv run python -m pytest tests/test_browser_device_example.py -q
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

排障顺序：

1. 先看 `/api/debug/devices`，确认设备在线、`user_id` 一致、能力声明正确。
2. 再看 Tool / Task 产物，确认模型是否真的调用了能力。
3. 再看 stream 产物，确认端侧是否上传或接收了数据。
4. 再看 assets 产物，确认图片、IMU 或深度图是否生成引用。
5. 最后看 Output Service 产物，确认用户可听见输出是否被播放仲裁处理。

## 13. 从本文迁移到新版 API

typed facade 会逐步替代兼容 API：

| 当前可用写法 | 新版目标写法 |
| --- | --- |
| `context.devices.request_asset("sensor.rgb", ...)` | `await context.devices.sensors.rgb.one(...)` |
| `context.devices.publish_event(..., stream_type="sensor.rgb")` | `context.devices.sensors.rgb.stream(...)` 或 `commands.call(...)` |
| `context.devices.watch_assets("sensor.rgb", ...)` | `async for frame in context.devices.sensors.rgb.stream(...)` |
| `context.devices.submit_text(...)` | `await context.output.say(...)` |
| `supports[].id` | `supports.sensors[].type` / `supports.actuators[].type` |

当前阶段可以在新 Tool 中使用已经落地的 typed facade；未落地部分仍按当前兼容 API 写，并在迁移时逐步替换。
