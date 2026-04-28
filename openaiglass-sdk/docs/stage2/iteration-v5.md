# SDK v6 迭代记录

本文记录 SDK 团队在 `sdk-v5` 之后，按欠缺能力优先级推进的第一轮能力补全。业务侧版本记录更新为 `sdk-v6`。

## 1. 输入反馈

本轮优先处理“手机视觉执行框架的资源管理”。功能团队后续会迁移更多手机视觉能力，如果 SDK 只负责把视频帧广播给任务，而不提供帧率限制、过载记录和可观察快照，业务层很容易重复实现帧队列、限流和资源保护。

本轮只做手机视觉资源管理的最小 SDK 能力，不处理以下事项：

1. 普通文本流式和 TTS 首包延迟。
2. 通知、抢播和用户打断策略。
3. 多设备组织和账号级管理。
4. Skill Runtime。
5. 任务持久化生产化。
6. 回放测试断言能力。
7. iOS 和 ESP32 SDK 打包形态。
8. 真实手机端模型资源池、YOLO 执行框架和多模型内存治理。

## 2. 本轮 SDK 改动

### 2.1 手机视觉任务资源策略

`PhoneRuntime` 新增 `VisionTaskPolicy`，用于从手机任务参数中读取视觉资源策略：

1. `min_frame_interval_ms`
2. `max_frames`
3. `priority`
4. `emit_overload_events`

策略优先从 `params["vision_policy"]` 读取。为了兼容早期写法，也会读取顶层 `frame_interval_ms`、`min_frame_interval_ms`、`max_frames` 和 `priority`。

### 2.2 资源策略调度

`PhoneRuntime.process_frame(...)` 和 `PhoneRuntime.process_task_frame(...)` 现在都会在调用业务任务 `on_frame(...)` 前执行资源策略检查。

当前支持两类过载原因：

1. `frame_rate_limited`：当前帧距离上一帧实际处理时间不足。
2. `max_frames_reached`：当前任务已达到最大处理帧数。

被 SDK 丢弃的帧不会进入业务任务，避免业务层自行处理资源限制。

### 2.3 手机任务快照增强

`PhoneTaskSnapshot` 新增：

1. `frames_dropped`
2. `resource_events`
3. `vision_policy`

其中 `resource_events` 会记录 `vision.task.overloaded`，供回放测试、`phone-mock` 和联调日志定位资源问题。

## 3. 开发者使用方式

功能开发者在启动手机任务时，可以通过任务参数声明策略：

```python
sdk.phone_runtime.start_task(
    task_type="demo_phone_task",
    params={
        "stream_id": "stream_cam_001",
        "vision_policy": {
            "min_frame_interval_ms": 1000,
            "max_frames": 30,
            "priority": 10,
            "emit_overload_events": True,
        },
    },
)
```

业务 `BasePhoneTask.on_frame(...)` 只会收到 SDK 允许处理的帧。被限流或丢弃的帧进入 `PhoneTaskSnapshot.resource_events`，不进入业务结果列表。

## 4. 本轮不进入 SDK 的内容

1. iOS 真机运行时还没有内置统一资源策略，真实 Swift 插件暂时需要读取同名 `vision_policy` 参数并保持一致语义。
2. `priority` 当前只进入策略和快照，后续再用于多任务抢占和降级。
3. 当前没有模型加载池、GPU/CPU 资源池、功耗治理和异步背压。
4. 当前没有把手机过载事件自动回流成服务端 TaskEvent；这会在通知和任务事件治理中继续补齐。

## 5. 文档同步

已同步更新：

1. `openaiglass-sdk/docs/structure-design/手机视觉资源管理设计.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`

## 6. 验证范围

新增测试覆盖：

1. 手机视觉任务按 `min_frame_interval_ms` 限制处理频率。
2. 达到 `max_frames` 后，后续帧不会进入业务任务。
3. 被 SDK 丢弃的帧会记录 `vision.task.overloaded`。
4. 快照中可以观察 `frames_processed`、`frames_dropped`、`resource_events` 和 `vision_policy`。

验证命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
python -m compileall -q openaiglass-sdk/server-python/openaiglasses/phone
```
