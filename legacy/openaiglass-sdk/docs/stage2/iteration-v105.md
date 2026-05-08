# sdk-v105 轮次记录器与最终边界验收

更新时间：2026-05-04

## 背景

`sdk-v104` 已经拆出 Omni 工具桥和 Text Agent Adapter，但 transcript artifact、输出音频 WAV 和 assistant 音频资产挂载仍散落在 `VoiceRuntime` 的多条模型管线中。这部分逻辑不属于具体模型服务，应该归入共享 turn recorder，供 Omni Server 和 Text Server 复用。

本轮作为 Omni Server / Text Server 四阶段拆分的收口迭代。

## 变更

1. 新增 `runtime/turn_recorder.py`。
   - `VoiceTurnRecorder` 统一构造输入音频资产、保存 transcript artifact、保存输出 WAV，并把 assistant 音频挂回 Agent 会话。
2. `VoiceRuntime` 的三条输出路径改为委托 turn recorder。
   - Text Agent + TTS 路径。
   - Omni persistent 长连接 prepared turn 路径。
   - Omni segment_turn 兼容路径。
3. import 边界测试扩大到 `runtime.turn_recorder`。
   - 断言新增共享 recorder 不能反向依赖 `runtime.voice_runtime`。
4. package-check 增加 `runtime.turn_recorder` 导入覆盖。

## 效果

`voice_runtime.py` 从 3874 行下降到 3852 行。当前四阶段拆分的代码边界已经收敛：

1. Phase 1：`VoiceGateway`、`VoiceServer`、`voice.server_mode` 配置边界已落地。
2. Phase 2：Omni Realtime 客户端、Omni 工具桥、播放流、通知桥接和 turn recorder 已迁出。
3. Phase 3：ASR/TTS 客户端、TextDialogStateMachine、TextAgentAdapter 和 turn recorder 已迁出。
4. Phase 4：package-check 与单测开始断言 Omni/Text/共享 recorder 的 import 边界。

## 验证

已执行：

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_realtime_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py \
  openaiglass-sdk/tests/unit/test_task_event_runtime.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q

uv run --python 3.11 --with pytest --with setuptools --with wheel \
  openaiglass.sdk.package-check --repo-root .
```

结果：相关单测 134 条通过，package-check 返回 `ok: true`。

设备级回放也已执行：

1. `look_look.json` 原始配置，音频为“我叫文刀文字的文刀锋的刀.wav”。
2. 临时配置 `/tmp/glass-playback-whoami-sdk-v105.json`，音频为“你是谁呀.wav”。

观察结果：

1. 服务端、phone-mock、glass-playback 均完成注册和绑定。
2. 眼镜回放完成 `voice.realtime.session.open`、音频段上传和旁路 ASR。
3. 两个文件段都没有触发 Omni semantic_vad 自动回复，服务端按 `semantic_vad_no_auto_response` 下发 `voice.dialog.close`，回放断言通过但 `actuator_count=0`。

结论：本轮设备级回放证明三端控制面、绑定、音频上传和失败收口没有回退；它不能替代真实眼镜连续对话、下行播放和模型自动 turn detection 的真机验收。

## 对业务开发者的影响

业务代码不需要修改。业务侧继续按 `BaseTool`、`BaseTask`、Skill、MCP 和 `DeviceGroupContext` 开发；不要导入 `runtime.turn_recorder`、`runtime.omni.tool_bridge` 或 `runtime.text.text_agent_adapter`，这些都是 SDK 内部模块。
