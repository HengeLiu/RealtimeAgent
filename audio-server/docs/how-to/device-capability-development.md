# 设备能力与 Context API 开发说明

当前架构只接受结构化 `supports`。Tool / Task 只能使用 typed facade：

- Tool：`context.devices.sensors.rgb.one()`、`context.devices.actuators.vibrator.one()`、`context.devices.commands.call()`。
- Task：在 Tool 能力基础上额外开放 `.stream()` 和 `commands.start()/subscribe_result()`。
- 用户可听输出：统一使用 `await context.output.say(...)`，不直接写 speaker。
- 麦克风和扬声器属于系统音频主链路，不作为普通 `supports` capability 暴露。

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
asset = await context.devices.sensors.rgb.one(
    params={"reason": "capture", "format": "jpeg"},
    timeout_seconds=2,
)
return ToolResult.success(data={"asset_id": asset.asset_id, "uri": asset.uri})
```

## Task 示例

```python
async for frame in context.devices.sensors.rgb.stream(fps=1, sample_count=3):
    handle_frame(frame)
await context.output.say("已完成画面分析", priority="normal")
```

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

不再存在 configure 事件。
