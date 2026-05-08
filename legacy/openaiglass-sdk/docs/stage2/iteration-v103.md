# sdk-v103 通知与 Task 事件语音桥接拆分

更新时间：2026-05-04

## 背景

通知和 Task 事件原先直接写在 `VoiceRuntime` 中，包括 `submit_notification(...)`、`TaskEvent` 回流 Agent、通知直接播报、通知中断和通知请求到播放流的映射。这部分能力是 SDK Core 与语音播放之间的桥接层，不属于 Omni 或 Text 模型主链路。

本轮把通知与 Task 事件到语音播放的桥接逻辑迁入独立模块。

## 变更

1. 新增 `runtime/notification_voice_bridge.py`。
   - 新增 `NotificationVoiceBridge`，负责外部通知提交、TaskEvent 处理、Agent 回流通知、通知播报和高优先级通知中断。
2. `VoiceRuntime` 保留迁移期兼容入口。
   - `submit_notification(...)`、`on_task_event(...)`、`_dispatch_notification_request(...)`、`_interrupt_notification_request(...)` 等入口仍存在，但实际委托给 bridge。
   - `_notification_coordinator` 仍是原对象；迁移期测试替换协调器时，bridge 会同步引用。
3. package-check 增加 `runtime.notification_voice_bridge` 导入覆盖。

## 效果

`voice_runtime.py` 从 4030 行下降到 3912 行。通知、TaskEvent、Agent 回流和播放流之间的桥接关系从模型管线中剥离，后续可以继续把 Omni 工具桥和 Text Agent Adapter 独立出来。

## 验证

已执行：

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_task_event_runtime.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q

uv run --python 3.11 --with pytest --with setuptools --with wheel \
  openaiglass.sdk.package-check --repo-root .
```

结果：相关单测 129 条通过，package-check 返回 `ok: true`。

## 对业务开发者的影响

业务代码不需要修改。`context.submit_notification(...)`、Task 终态事件、通知优先级、通知合并和通知中断策略保持原有使用方式。
