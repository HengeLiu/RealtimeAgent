# 第一个能力工具

`realtime-agent` 把所有模型可调用能力统一表达为 **Tool**。一次能力调用由 SDK 建模成一个可
追踪的 Tool Run：

- **前台短能力**（默认 `fail_fast`）：拍照、查时间、查当前位置等，模型等待结果后继续当前回复。
- **后台能力**（`late_result_policy="background"`）：找物、导航路线、联网搜索、计时器等耗时不
  稳定的能力。调用先返回“正在处理”，结果就绪后由系统按会话状态送回模型组织回复或直接播报。

开发者只需实现 `BaseTool`，能力差异由 `ToolSpec` 声明。不需要直接操作 WebSocket，业务代码通过
SDK 注入的 Context API 使用设备能力。（历史上的 Task 概念已并入 Tool，不再单独维护。）

## 应用目录结构

推荐应用结构：

```text
examples/<your-app>/agent-server/
  server.yaml
  capabilities/
    __init__.py
    tools.py
```

当前可参考最小 server 配置：

```text
dev-support/agent-server/
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

## 写一个后台能力工具

耗时不稳定或需要持续运行的能力声明 `late_result_policy="background"`。调用超过等待窗口
（默认 3 秒）未完成时，工具先返回“正在处理”，模型据此告诉用户稍候；结果就绪后由 SDK 的
FollowUpRouter 按会话状态送回模型或直接播报。SDK 已内置 `start_timer` 工具用于倒计时、稍后
提醒和到点提示。下面是一个等效的后台计时能力示例：

```python
import asyncio

from pydantic import BaseModel, Field

from realtime_agent import BaseTool, ToolContext, ToolResult, ToolSpec


class TimerInput(BaseModel):
    """计时器启动参数。"""

    seconds: int = Field(ge=0, description="计时时长，单位秒。")
    message: str = Field(default="", description="到点时播报给用户的话。")


class TimerTool(BaseTool):
    """后台等待指定秒数后到点提醒。"""

    spec = ToolSpec(
        name="start_timer",
        description="启动计时器：倒计时、稍后提醒或到点提示。",
        input_model=TimerInput,
        late_result_policy="background",   # 超窗转后台，结果稍后送达
        late_result_notify="direct",        # 到点直接播报，不必再经模型组织
        cancel_supported=True,              # 可经 tool_run_manager 取消
        running_message="计时器已开始计时。",
    )

    def background_timeout_seconds_for(self, input_data: dict) -> float:
        """按计时秒数给后台总超时预算留出余量。"""

        return float(input_data.get("seconds") or 0) + 30.0

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """后台等待到点并返回提醒文案。"""

        seconds = max(0, int(input_data.get("seconds") or 0))
        message = str(input_data.get("message") or "").strip() or "时间到了。"
        if seconds > 0:
            await asyncio.sleep(seconds)
        return ToolResult.success(message=message)
```

要点：

1. background 工具的 `run()` 在后台 runner 上执行，可以驻留较久、`await` 设备命令或定时。
2. 需要持续 stream 或长命令时，background 工具的 `context.devices` 自动获得长命令能力，可用
   `handle = await context.devices.commands.start(...)` 并 `async for event in handle.results()`
   内联消费端侧回报；被取消时在 `except asyncio.CancelledError` 中清理端侧资源。
3. 取消、查询、列出后台运行由内置 `tool_run_manager` 工具承载。

## 让 SDK 发现能力

示例应用通过 `server.yaml` 配置 Tool 自动发现。开发者把 `BaseTool` 子类放进配置的包里，启动时 SDK 会扫描并注册。

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
uv run realtime-agent.server.run --config dev-support/agent-server/server.yaml
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
find dev-support/agent-server/runs -maxdepth 4 -type f | sort
```

排查模型请求、工具调用和设备事件时，优先看 [runs 目录产物说明](../../agent-server/docs/how-to/运行产物排查说明.md)。
