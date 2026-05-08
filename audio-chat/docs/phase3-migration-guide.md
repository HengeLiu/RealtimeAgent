# audio-chat Phase 3 业务迁移指南

更新时间：2026-05-07

本文面向从旧 `openaiglass-sdk` 迁移业务能力到 `audio-chat` 的开发人员。目标不是复刻旧目录，而是把能力改写到新的“用户语音会话 + event + stream”协议模型上。

## 1. 迁移边界

`audio-chat` server 只负责设备注册绑定、事件订阅分发、stream 生命周期、资产缓存、Agent Core、Tool / Task 扩展、Output Service、播放仲裁、回放验收和开发者工具链。

端侧继续负责麦克风录音、喇叭播放、唤醒词、AEC、摄像头驱动和本地硬件控制。server 不新增隐藏 RPC，也不直接控制某个端侧硬件。

## 2. 旧 SDK 概念映射

| 旧概念 | audio-chat 概念 | 迁移说明 |
| --- | --- | --- |
| `DeviceGroupContext` | `UserDeviceContext` | 以 `user_id` 的 active device set 为边界，按 capability 和 subscription 选择端侧。 |
| 抓拍工具 | `BaseTool` + `request_asset("sensor.rgb")` | 控制事件只请求采集策略，图片字节必须通过 `sensor.rgb` stream 上传。 |
| 长流程 Task | `BaseTask` + `TaskEventBridge` | Task 只维护 server 侧状态，通过 event 和 stream 驱动端侧。 |
| 语音播报 | `Output Service` + `submit_text()` | 业务只提交文本、优先级和 TTL，不直接写播放器。 |
| 手机任务 | 端侧 capability + subscription | 端侧声明能处理哪些事件，server 不新增 `start_phone_task` RPC。 |
| 媒体帧 | `StreamChunk` | 大字节统一走 stream，不放进控制事件 payload。 |
| `context.mcp(...)` | `McpGateway` 或业务 Tool wrapper | MCP 不直接拿设备上下文；需要设备能力时通过 Tool / Task 间接调用。 |
| Agent Memory | `MemoryService` + 内置 Tool | Memory 注入模型上下文，设备能力仍通过普通 Tool / Task 表达。 |
| `context.submit_notification(...)` | `UserDeviceContext.submit_text(...)` / `TaskEventBridge` | 通知进入 Output Service 和播放仲裁，不直接操作播放器。 |

## 3. 强制约束

1. Tool / Task 只能通过 `UserDeviceContext` 使用设备能力。
2. 不允许硬编码 device_id 做点对点发送。
3. 设备通讯只能使用 event 和 stream，不新增隐藏 RPC。
4. 大字节媒体必须走 stream，控制事件 payload 只放语义、配置、关联 ID 和小型状态。
5. MCP、Skill、Memory 不允许直接持有设备上下文；需要设备能力时必须封装成 Tool 或 Task。
6. 后台任务通知必须进入 Output Service 或 TaskEventBridge，不直接操作播放队列。

## 4. 可复制样板

当前仓库提供三类迁移起点：

| 样板 | 文件 | 适用场景 |
| --- | --- | --- |
| 找物 Tool | `examples/migration-templates/find_object/tool.py` | 一次性请求 RGB 资产，再由模型或视觉处理器分析。 |
| 连续 RGB Task | `examples/migration-templates/continuous_rgb_analyze/task.py` | 通过事件配置连续上传，并通过 `watch_assets()` 消费多帧图片。 |
| 通知 Task | `examples/migration-templates/notification_task/task.py` | 后台任务到点、状态变化或异常提醒。 |

复制到业务 app-root 时，建议目录如下：

```text
my-app/
  capabilities/
    find_object/
      tool.py
    continuous_rgb_analyze/
      task.py
    notification_task/
      task.py
  host/
    server/
      main.py
  config/
    server.yaml
```

`server.yaml` 中打开自动发现后，Tool / Task 不需要改 SDK 内部代码：

```yaml
tools:
  discover:
    enabled: true
    recursive: true
    packages:
      - capabilities
tasks:
  discover:
    enabled: true
    recursive: true
    packages:
      - capabilities
```

## 5. BaseTool 迁移

旧 SDK 的 Tool 常见写法是继承 `BaseTool`，在 `run(context, input_data)` 中通过 `DeviceGroupContext` 使用设备能力。迁移后仍然继承 `BaseTool`，但参数声明改为 `ToolSpec + Pydantic`：

```python
from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class FindObjectInput(BaseModel):
    """找物 Tool 输入参数。"""

    object_name: str = Field(default="目标物", description="用户想要查找的物品名称。")
    timeout_seconds: float = Field(default=2, gt=0, description="等待端侧上传图片资产的超时时间，单位秒。")


class FindObjectTool(BaseTool):
    """找物 Tool 迁移样板。"""

    spec = ToolSpec(
        name="find_object",
        description="请求端侧采集图片，并准备一次找物分析。",
        input_model=FindObjectInput,
        progress_message="正在请求端侧画面",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        asset = context.devices.request_asset(
            "sensor.rgb",
            freshness_seconds=0,
            timeout_seconds=input_data["timeout_seconds"],
            configure_payload={"reason": "find_object", "object_name": input_data["object_name"]},
        )
        if asset is None:
            return ToolResult.success(data={"captured": False}, message="未收到端侧画面")
        return ToolResult.success(data={"captured": True, "asset_id": asset.asset_id}, assets=[asset])
```

迁移规则：

1. 保留业务参数校验、模型可读 `description` 和短动作语义。
2. 使用 `ToolSpec.name/description/input_model` 告诉 Agent Core 这个工具叫什么、做什么、需要哪些参数。
3. `input_model` 使用 Pydantic `BaseModel`，字段类型和 `Field(description=...)` 会自动转换成模型可见的 provider tool schema。
4. SDK 会在调用 `run()` 前校验参数并填充默认值；`run()` 收到的是校验后的 `dict`。
5. 把旧的 `context.capture_photo()` 改成 `context.devices.request_asset("sensor.rgb", ...)`。
6. 把旧的 `context.submit_notification()` 改成 `context.devices.submit_text(...)`。
7. 不再保存或传递 `device_id`；通过 `require_capability`、`stream_type` 和 subscription 匹配设备。
8. Tool 返回 `ToolResult.success(...)`，资产用 `assets=[asset]` 带回，图片字节不进 `data`。

参考文件：`examples/migration-templates/find_object/tool.py`。

## 6. BaseTask 迁移

旧 SDK 的长流程 Task 通常负责状态机、手机任务、设备事件和通知。迁移后 Task 仍然继承 `BaseTask`，但事件、资产和输出都走 `TaskContext.devices` 与 `TaskEventBridge`：

```python
from audio_chat import BaseTask, TaskContext, TaskEvent
```

迁移规则：

1. `on_start()` 只发布控制事件或初始化 server 侧状态。
2. 连续图片、深度图、IMU 和音频窗口必须通过 `sensor.*` stream 进入 Asset Service。
3. Task 消费连续数据时使用 `context.devices.watch_assets(...)`。
4. 状态变化通过 `TaskEvent` 回流；需要用户听见时交给 Output Service。
5. 取消任务时发布停止配置事件，例如 `stream.control.configure.requested` + `payload={"mode": "stop"}`。

参考文件：

- `examples/migration-templates/continuous_rgb_analyze/task.py`
- `examples/migration-templates/notification_task/task.py`

## 7. DeviceGroupContext 到 UserDeviceContext

`UserDeviceContext` 是旧 `DeviceGroupContext` 的开发者等价入口，但语义变为“当前用户的 active device set”。它不暴露可变连接对象，也不提供按 `device_id` 点对点发送。

| 旧写法 | 新写法 | 说明 |
| --- | --- | --- |
| `context.require_glass()` | `context.devices.find_device("sensor.rgb")` | 只返回只读能力快照，真实通讯继续走 event / stream。 |
| `context.require_phone()` | `context.devices.find_device("vision.local")` | phone 不再是固定类型，而是 capability。 |
| `context.capture_photo()` | `context.devices.request_asset("sensor.rgb", ...)` | 图片字节通过 `sensor.rgb` stream 上传。 |
| `context.start_phone_video_link()` | `context.devices.publish_event("stream.control.configure.requested", stream_type="sensor.rgb", ...)` | 持续视频变为传感器 stream 配置。 |
| `context.submit_notification()` | `context.devices.submit_text(...)` | 输出交给 Output Service。 |
| `context.mcp("amap.route_plan", ...)` | `context.mcp.call(...)` 或业务 Tool wrapper | MCP 不直接控制设备。 |

## 8. 抓拍和视觉资产

旧 SDK 的 `capture_photo` 是控制面请求加图片回传。新 SDK 的等价路径是：

1. Tool 发布或间接触发 `stream.control.configure.requested`。
2. 端侧收到事件后打开或写入 `sensor.rgb` stream。
3. Asset Service 把 JPEG / PNG 缓存为 `AssetRef`。
4. Tool 返回 `AssetRef`，Agent 或后续 Task 读取引用。

推荐便捷 API：

```python
asset = context.devices.request_asset(
    "sensor.rgb",
    freshness_seconds=0,
    configure_payload={"reason": "find_object", "format": "jpeg"},
    timeout_seconds=2,
)
```

## 9. Phone Video Task 迁移

旧 SDK 中 `start_phone_video_link()` / phone task 用于让眼镜和手机建立视觉链路。新 SDK 不新增隐藏 phone RPC，迁移为 capability + subscription：

旧文档里也可以把这条链路称为 phone video task；迁移时不要保留点对点视频 RPC，而是改成 `sensor.rgb` stream 和事件订阅。

1. phone mock 或 iOS 参考端注册 `streams.produce=["sensor.rgb"]` 或视觉处理 capability。
2. Task 发布 `stream.control.configure.requested`，带 `stream_type="sensor.rgb"`、`mode="continuous"`、`fps`、`correlation_id`。
3. 端侧通过 `sensor.rgb` stream 上传帧。
4. Task 用 `watch_assets("sensor.rgb", correlation_id=...)` 消费帧。
5. 检测结果用事件或 TaskEvent 回流，用户提示进入 Output Service。

## 10. MCP Adapter 迁移

旧 SDK 业务能力常把地图、搜索等外部服务注册成 MCP Adapter。新 SDK 保留 MCP 能力面，但迁移后要遵守两条边界：

1. MCP 只处理外部服务调用、结构化输入输出、超时和错误分类。
2. MCP 不直接拿 `UserDeviceContext`；如果路线规划后需要端侧导航提示，应由 Tool / Task 调用 MCP 后再通过 `context.devices.publish_event(...)` 或 `submit_text(...)` 表达。

地图和搜索能力迁移建议：

- `navigation`：Tool 调 MCP 规划路线，Task 监听位置、航向和视觉事件。
- `search`：Tool 调搜索 MCP，返回模型可读摘要和引用。

## 11. Memory 迁移

旧 SDK 的 Agent Memory 在新 SDK 中对应 `MemoryService` 和内置 memory Tool。迁移原则：

1. 用户偏好、常用地点、设备习惯等写入 Memory。
2. 当前视觉资产、音频片段和任务状态不要塞进 Memory，应该用 Asset / Task 产物。
3. Memory 不能直接触发设备动作；需要设备能力时由 Tool / Task 执行。

## 12. Notification 迁移

旧 SDK 的通知、TTS 和播放仲裁在新 SDK 中收敛到 Output Service。迁移规则：

1. 一次性提示用 `context.devices.submit_text(text, priority=..., ttl_seconds=...)`。
2. Task 状态变化优先发 `TaskEvent`，由 `TaskEventBridge` 决定是否直出通知或交给 Agent。
3. 不直接操作 speaker stream，除非业务确实要发送原生音频，此时使用 `submit_audio(...)` 或 `open_output_stream("actuator.speaker", ...)`。
4. 被仲裁丢弃或排队时看 `output-decisions.jsonl`。

## 13. Playback Config 迁移

旧 `glass-playback` 配置中的触发音频、抓拍、视频流、传感器时间线和执行器断言，在新 SDK 中应落到以下概念：

| 旧配置意图 | 新配置意图 |
| --- | --- |
| trigger audio | 打开 `sensor.mic` stream 并上传音频 chunk。 |
| camera capture | 响应 `stream.control.configure.requested` 后上传一帧 `sensor.rgb` 资产。 |
| camera stream | 按 `fps` 连续上传 `sensor.rgb` stream。 |
| heading / location | 作为小型语义事件或 `sensor.imu` / `sensor.location` 资产。 |
| speaker playback assertion | 记录 `actuator.speaker` output stream 与 Output Service 决策。 |

最小回放入口：

```bash
uv run audio-chat.playback.glass --config audio-chat/examples/minimal/playback.yaml
```

业务迁移样板入口：

```bash
uv run audio-chat.playback.glass --config audio-chat/examples/basic-app/host/glass-playback/playback.yaml
```

## 14. 老业务能力样板映射

| 旧能力 | audio-chat 样板目录 | 迁移重点 |
| --- | --- | --- |
| find_object | `examples/for-blind-app/capabilities/find_object` | 一次性 `sensor.rgb` 资产请求，检测结果进入 ToolResult 或 TaskEvent。 |
| traffic_light | `examples/for-blind-app/capabilities/traffic_light` | 连续 `sensor.rgb` stream，识别结果通过 TaskEvent 和 Output Service 提醒。 |
| navigation | `examples/for-blind-app/capabilities/navigation` | MCP 路线准备 + 位置/航向/视觉事件驱动 Task。 |
| search | `examples/for-blind-app/capabilities/search` | 搜索 MCP wrapper，返回摘要和引用，不碰设备连接。 |
| timer | `examples/for-blind-app/capabilities/timer` | Task 调度、恢复、取消和通知仲裁。 |

## 15. 迁移流程

1. 先跑当前 SDK 基线：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py developer-usability \
  --report runs/acceptance/developer-usability.json
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

2. 从 `examples/migration-templates` 复制最接近的样板。
3. 把旧代码中的直接设备调用改为 `context.devices.publish_event()`、`request_asset()`、`watch_assets()`、`open_output_stream()` 或 `submit_text()`。
4. 把图片、音频、视频等大字节移到 `sensor.*` 或 `actuator.*` stream。
5. 为能力补一个独立回放或契约测试。
6. 跑 H 线路文档契约验收：

```bash
cd audio-chat
uv run python scripts/acceptance_check.py next-docs-contract \
  --report runs/acceptance/next-docs-contract.json
```

## 16. 联调观察点

迁移后的能力至少观察这些产物：

1. `runs/audio-chat/.../events.jsonl`：注册、订阅匹配、任务状态和控制事件。
2. `runs/audio-chat/.../stream-events.jsonl`：输入和输出 stream 生命周期。
3. `runs/audio-chat/.../assets.jsonl`：`sensor.rgb` 等资产缓存引用。
4. `runs/audio-chat/.../tool-events.jsonl`：Tool 入参、结果和错误。
5. `runs/audio-chat/.../task-events.jsonl`：Task 状态、事件和通知决策。
6. `runs/audio-chat/.../output-decisions.jsonl`：Output Service 和播放仲裁结果。
7. `runs/audio-chat/.../result.json`：回放最终摘要。

如果这些产物缺失，应优先补回放和记录器，而不是只看日志文本。

## 17. 老 SDK 主要开发者入口迁移表

| 老 SDK 入口 | audio-chat 入口 | 当前状态 |
| --- | --- | --- |
| `openaiglass.config.sync` | `audio-chat.config.sync` | 已有 CLI 和开发样例配置。 |
| `openaiglass.server.run` | `audio-chat.server.run` | 已有 YAML server 启动入口。 |
| `openaiglass.phone.mock` | `audio-chat.phone.mock` | 已有 Python phone mock 参考端。 |
| `openaiglass.glass.start --runtime playback` | `audio-chat.playback.glass` | 已有 Python playback 入口。 |
| `openaiglass.phone.open` | `endpoints/ios-phone` | 当前为 iOS 参考端目录，CLI 由 `old-sdk-parity-cli` 补齐。 |
| `openaiglass.glass.start` | `endpoints/esp32-s3` | 当前为 ESP32-S3 参考端目录，构建烧录由 `old-sdk-parity-esp32` 补齐。 |
| `openaiglass.sdk.preflight` | `audio-chat.dev.preflight` | 已有预检报告。 |
| `BaseTool` / `BaseTask` | `audio_chat.BaseTool` / `audio_chat.BaseTask` | 顶层公开 API。 |
| `DeviceGroupContext` | `audio_chat.UserDeviceContext` | 通过 event / stream / asset / output 表达设备能力。 |
