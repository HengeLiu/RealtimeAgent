# 设备能力与 Context API 开发说明

当前仓库已经可用的设备能力开发入口是 structured supports 加 typed facade。当前架构只接受结构化 `supports`，Tool / Task 只能使用 typed facade：

- Tool：`context.devices.sensors.rgb.one()`、`context.devices.actuators.vibrator.one()`、`context.devices.commands.call()`。
- Task：在 Tool 能力基础上额外开放 `.stream()` 和 `commands.start()/subscribe_result()`。
- 用户可听输出：统一使用 `await context.output.say(...)`，不直接写 speaker。
- 麦克风和扬声器属于系统音频主链路，不作为普通 `supports` capability 暴露。

Tool / Task 通过 typed facade 使用设备能力。
当前新 Tool 可以优先试用 typed facade，只有确实缺少公开 facade 时再补 SDK 能力面。

## 设备能力文件

```yaml
supports:
  sensors:
    - type: rgb
      modes: [single, continuous]
      default:
        format: jpeg
        frequency_hz: 1
        sample_count: 1
    - type: imu
      modes: [continuous]
      default:
        frequency_hz: 30
    - type: tof
      modes: [single, continuous]
      default:
        format: png
        frequency_hz: 5
  actuators:
    - type: vibrator
      commands: [vibrate]
```

## Tool 示例

```python
from realtime_agent import BaseTool, ToolContext, ToolResult, ToolSpec


asset = await context.devices.sensors.rgb.one(
    params={"reason": "capture", "format": "jpeg"},
    timeout_seconds=2,
)
return ToolResult.success(data={"asset_id": asset.asset_id, "uri": asset.uri})
```

## Task 示例

```python
from realtime_agent import BaseTask, TaskContext


async for frame in context.devices.sensors.rgb.stream(fps=1, sample_count=3):
    handle_frame(frame)
await context.output.say("已完成画面分析", priority="normal")
```

`context.assets.get(asset_id)` 的返回值是 `AssetRef | None`；找不到资产时业务代码应给出明确失败结果。

## 命令协议

远程命令只使用 `command.*`：

- `command.requested`
- `command.accepted`
- `command.progress`
- `command.completed`
- `command.failed`

`commands.call()` 会等待端侧 `completed/failed` 回报；没有回报时按超时失败处理。

## Stream 控制协议

传感器控制只使用：

- `stream.control.open.requested`
- `stream.control.close.requested`

## 常用命令和观察点

```bash
uv run realtime-agent.device.validate examples/dev-support/devices/browser-glass/device.realtime-agent.yaml
uv run realtime-agent.server.run --app-name for-blind-app
uv run realtime-agent.web.open --serve
```

for-blind-app 默认运行产物位于 `examples/for-blind-app/audio-server/runs/`。一次
session 的资产事件位于 `<runs_root>/<user_id>/<device_id>/assets.jsonl`，等价结构也可按
`runs/<app_name>/<user_id>/<device_id>/assets.jsonl` 理解。
