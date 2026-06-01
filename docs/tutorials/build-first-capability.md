# 第一个 Tool 和 Task

`realtime-agent` 推荐把业务能力分成 Tool 和 Task。

- **Tool**：一次性短动作，例如拍照、查路线、搜索资料。
- **Task**：持续或后台动作，例如找物、红绿灯观察、计时器、导航执行过程。

开发者不需要直接操作 WebSocket。业务代码通过 SDK 注入的 Context API 使用设备能力。

## 应用目录结构

推荐应用结构：

```text
examples/<your-app>/agent-server/
  server.yaml
  capabilities/
    __init__.py
    tools.py
    tasks.py
```

当前可参考最小 server 配置：

```text
examples/dev-support/agent-server/
```

## 写一个 Tool

Tool 适合处理一次性动作。下面是简化后的抓拍 Tool 结构：

```python
from pydantic import BaseModel, Field

from realtime_agent import BaseTool, ToolContext, ToolResult, ToolSpec


class CapturePhotoInput(BaseModel):
    """抓拍输入参数。"""

    reason: str = Field(default="agent_requested", description="请求抓拍的业务原因。")
    timeout_seconds: float = Field(default=5, gt=0, description="等待图片返回的超时时间。")


class CapturePhotoTool(BaseTool):
    """通过端侧 RGB 传感器抓拍当前画面。"""

    spec = ToolSpec(
        name="capture_photo",
        description="当用户需要了解当前画面、障碍物、文字或路况时，采集一张当前 RGB 图片。",
        input_model=CapturePhotoInput,
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行抓拍并返回资产引用。"""

        asset = await context.devices.sensors.rgb.one(
            params={"reason": input_data.get("reason", "agent_requested"), "format": "jpeg"},
            timeout_seconds=float(input_data.get("timeout_seconds") or 5),
        )
        return ToolResult.success(
            data={"asset_id": asset.asset_id, "uri": asset.uri, "mime_type": asset.mime_type},
            assets=[asset],
            message="已获取当前画面。",
        )
```

关键点：

1. Tool 不写底层事件名。
2. Tool 不硬编码 `device_id`。
3. 图片字节通过 stream 上传，Tool 返回资产引用。
4. 用户可听输出应通过 `context.output.say()` 或 ToolResult message 表达。

## 写一个 Task

Task 适合处理持续动作或后台动作。下面是简化后的计时器 Task 结构：

```python
from pydantic import BaseModel, Field

from realtime_agent import BaseTask, TaskContext


class TimerTaskInput(BaseModel):
    """计时器启动参数。"""

    seconds: int = Field(ge=1, description="计时器时长，单位秒。")
    message: str = Field(default="", description="计时结束时播报给用户的话。")


class TimerTask(BaseTask):
    """到点后通过 Output Service 播报提醒。"""

    task_type = "timer_task"
    description = "启动计时器后台任务。"
    input_model = TimerTaskInput

    async def on_start(self, context: TaskContext) -> None:
        """启动计时器。"""

        input_data = dict(context.metadata.get("input") or {})
        seconds = int(input_data["seconds"])
        message = input_data.get("message") or "时间到了"
        await context.schedule_signal("timer.due", delay_seconds=seconds, payload={"message": message})
```

真实任务还需要处理完成、失败、取消和通知。建议先在自己的 app-root 下补充
`capabilities/tasks.py`，再通过自动发现注册。


## 让 SDK 发现能力

示例应用通过 `server.yaml` 配置 Tool / Task 自动发现。开发者把 `BaseTool` 或 `BaseTask` 子类放进配置的包里，启动时 SDK 会扫描并注册。

检查配置：

```yaml
tools:
  discover:
    enabled: true
    recursive: true

tasks:
  discover:
    enabled: true
    recursive: true
```

## 验证能力

启动 server：

```bash
uv run realtime-agent.server.run --config examples/dev-support/agent-server/server.yaml
```

打开浏览器眼镜模拟组件：

```bash
uv run realtime-agent.web.open --serve
```

查看设备状态：

```bash
curl http://127.0.0.1:8765/api/debug/devices
```

查看运行产物：

```bash
find examples/dev-support/agent-server/runs -maxdepth 4 -type f | sort
```

排查模型请求、工具调用和设备事件时，优先看 [runs 目录产物说明](../../agent-server/docs/how-to/运行产物排查说明.md)。
