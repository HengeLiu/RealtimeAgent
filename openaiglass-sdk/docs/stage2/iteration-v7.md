# SDK v8 迭代记录

本文记录 SDK 团队在 `sdk-v7` 之后，按欠缺能力优先级推进的第三轮能力补全。业务侧版本记录更新为 `sdk-v8`。

## 1. 输入反馈

本轮处理“通知、抢播和用户打断策略”。此前 SDK 已有 `NotificationCoordinator`，但策略主要依赖 `allow_interrupt` 布尔值，运行态也缺少“为什么这条通知被播报、排队、抢播或去重”的解释。

本轮先补通知仲裁最小闭环，不处理完整实时语音用户打断。

## 2. 本轮 SDK 改动

### 2.1 显式通知策略

`NotificationRequest` 新增：

1. `interrupt_policy`
2. `resume_policy`

兼容旧字段：

1. `allow_interrupt=true` 且未设置 `interrupt_policy` 时，默认 `higher_priority`。
2. `allow_interrupt=false` 且未设置 `interrupt_policy` 时，默认 `never`。

当前支持策略：

1. `never`
2. `higher_priority`
3. `critical_only`
4. `always`

### 2.2 仲裁结果和决策快照

`NotificationSubmitResult` 新增：

1. `reason`
2. `active_request_id`
3. `queued_position`

新增 `NotificationDecision`，记录直发、排队、抢播和去重原因。

`NotificationCoordinator.build_snapshot()` 输出：

1. `active_requests`
2. `pending_requests`
3. `recent_decisions`

### 2.3 VoiceRuntime 运行态聚合

`VoiceRuntime.build_runtime_snapshot()` 新增：

1. `active_notification`
2. `pending_notifications`
3. `recent_notification_decisions`

抢播旧通知时，`actuator.audio.interrupt` 的 payload 会包含 `resume_policy`，为后续恢复播放策略预留接口。

## 3. 本轮不进入 SDK 的内容

1. 不实现完整实时语音用户打断。
2. 不把普通 Agent 回复、任务通知和视觉告警全部收敛到统一播放仲裁器。
3. 不实现被中断内容的恢复播放，目前默认 `drop_interrupted`。
4. 不修改眼镜端音频播放协议。

## 4. 文档同步

已同步更新：

1. `openaiglass-sdk/docs/structure-design/通知抢播与用户打断策略设计.md`
2. `openaiglass-for-blind/SDK安装与能力开发指南.md`
3. `openaiglass-for-blind/sdk-version`
4. `SDK对功能开发支持情况的说明.md`

## 5. 验证范围

新增和调整测试覆盖：

1. 通知去重结果会带原因。
2. `critical_only` 策略下，high 通知只排队，critical 通知抢播。
3. 通知协调器快照能导出活动通知、待播队列和最近决策。
4. `VoiceRuntime` 运行态快照能聚合通知仲裁状态。
5. 旧通知被抢播时继续下发 `actuator.audio.interrupt`。

验证命令：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_task_event_runtime.py -q
python -m compileall -q openaiglass-sdk/server-python/runtime
```
