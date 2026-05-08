# sdk-v106 继续压缩 VoiceRuntime 系统辅助职责

更新时间：2026-05-05

## 背景

`sdk-v105` 完成了四阶段代码边界收口，但复查后仍有一类未完成事项：`voice_runtime.py` 仍然承载会话历史消息构造、旁路 ASR 回填和连续对话关闭状态机等系统辅助逻辑。它们不属于设备控制面，也不属于具体模型客户端，继续留在 `VoiceRuntime` 会让后续排障和迭代理解成本偏高。

本轮继续做低风险拆分，不改变三端协议、模型调用方式、播放队列或业务开发 API。

## 变更

1. 新增 `runtime/message_builder.py`。
   - `VoiceMessageBuilder` 负责把系统提示词、短期历史和当前用户文本组装成模型 messages。
   - `VoiceRuntime._build_model_messages(...)` 保留迁移期兼容入口，实际委托 builder。
2. 新增 `runtime/sidecar_transcript.py`。
   - `SidecarTranscriptBackfiller` 负责旁路 ASR 晚到后的 Agent 会话文本回填和 transcript artifact 重写。
3. 新增 `runtime/continuous_dialog.py`。
   - `ContinuousDialogManager` 负责 `voice.dialog.close` 下发、停止指令清理、模型工具请求“播报完成后关闭”和播放完成后的延迟关闭。
4. import 边界测试和 package-check 增加新模块覆盖。
   - `runtime.message_builder`、`runtime.sidecar_transcript`、`runtime.continuous_dialog` 均不能反向依赖 `runtime.voice_runtime`。

## 效果

`voice_runtime.py` 从 3852 行下降到 3675 行。当前 `VoiceRuntime` 仍是迁移期门面，但会话消息构造、旁路 ASR 回填、连续对话关闭、播放、通知、进度播报缓存、轮次记录、Omni 工具桥和 Text Agent Turn 构造都已拆成独立模块。

## 验证

已执行：

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py::VoiceRuntimeTestCase::test_build_model_messages_uses_text_history \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py -q

uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py \
  openaiglass-sdk/tests/unit/test_task_event_runtime.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q

uv run --python 3.11 --with pytest --with setuptools --with wheel \
  openaiglass.sdk.package-check --repo-root .

LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-sdk-v106-register-check.log

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/register_only.json \
  --max-runtime-seconds 5
```

结果：

1. 相关单测 134 条通过。
2. package-check 返回 `ok: true`。
3. register-only 设备级回放完成眼镜注册、自动绑定、`voice.realtime.session.open` 和 `voice_server_mode=omni_server` 快照检查；该回放不包含触发音频和下行播放，因此不覆盖真实连续对话和播放链路。

## 对业务开发者的影响

业务代码不需要修改。业务侧继续通过 `BaseTool`、`BaseTask`、Skill、MCP 和 `DeviceGroupContext` 使用 SDK，不需要导入本轮新增的内部模块。
