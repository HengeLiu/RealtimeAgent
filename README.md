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
uv run audio-chat.phone.mock --config device-examples/python-phone/phone.mock.yaml
```

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

iOS 参考端：

```bash
uv run audio-chat.ios.open
```

ESP32-S3 参考端：

```bash
uv run audio-chat.esp32.config
uv run audio-chat.esp32.build --dry-run --build-only
```

## 应用开发

功能扩展开发者只依赖 `audio_chat` 顶层公开 API，不直接 import SDK 内部服务对象，也不硬编码 `device_id` 点对点发送事件。

Tool 用于一次性能力，例如拍照、搜索、准备路线：

```python
from pydantic import BaseModel, Field

from audio_chat import BaseTool, ErrorCode, ToolContext, ToolError, ToolResult, ToolSpec


class CaptureInput(BaseModel):
    reason: str = Field(default="agent_requested", description="请求端侧上传图片的原因。")


class CapturePhotoTool(BaseTool):
    spec = ToolSpec(
        name="capture_photo",
        description="当用户需要了解眼前画面时，请求当前用户设备上传一张 sensor.rgb 图片。",
        input_model=CaptureInput,
        progress_message="我先拍张照片看看。",
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        asset = context.devices.capture_photo(
            reason=str(input_data.get("reason") or "agent_requested"),
            freshness_seconds=0,
            timeout_seconds=10,
        )
        if asset is None:
            return ToolResult.failed(ToolError("拍照超时", code=ErrorCode.TIMEOUT))
        return ToolResult.success({"asset_id": asset.asset_id, "path": asset.path}, assets=[asset])
```

Task 用于长流程能力，例如连续视觉分析、导航执行期、计时器和后台通知：

```python
from audio_chat import BaseTask, TaskContext, TaskEvent


class ContinuousVisionTask(BaseTask):
    task_type = "continuous_vision"
    description = "连续读取 sensor.rgb 资产并分析。"

    async def on_start(self, context: TaskContext) -> None:
        context.devices.configure_stream("sensor.rgb", mode="continuous", rate_hz=1)

    async def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        await context.emit_progress("视觉任务正在运行")
```

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

设备注册时提交订阅策略。server 只在同一个 `user_id` 当前在线设备中投递事件，先匹配事件名，再匹配 `filter` 字段。

最小注册事件：

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
      "camera.facing": "front"
    }
  }
}
```

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
