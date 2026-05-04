# sdk-v98 Omni/Text Server 适配器落地

更新时间：2026-05-04

## 背景

`sdk-v97` 已经新增 `voice.server_mode` 和 `VoiceGateway`。本轮继续推进设计文档第 13 节 Phase 2、Phase 3 和 Phase 4，但保持一个关键约束：真机语音链路已经多轮修复过，不能为了物理搬文件一次性重写 Realtime 回调、播放仲裁、ASR 和 TTS 热路径。

因此本轮采用 adapter-first 的迁移方式：先让代码边界、配置选择和状态机归属稳定，再逐步迁移内部实现。

## 变更

1. 新增 `runtime/omni/omni_voice_server.py`。
   - `OmniVoiceServer` 只在 `VOICE_SERVER_MODE=omni_server` 下可用。
   - 当前委托已稳定的 `VoiceRuntime` Omni 热路径。
2. 新增 `runtime/text/text_voice_server.py`。
   - `TextVoiceServer` 只在 `VOICE_SERVER_MODE=text_server` 下可用。
   - 当前委托已稳定的 ASR -> Agent -> TTS 热路径。
3. 新增 `runtime/text/text_dialog_state_machine.py`。
   - Text Server 的停止指令、空文本、语气词、助手回声和短连续 VAD 文本规则集中到 `TextDialogStateMachine`。
   - `VoiceRuntime` 的文本裁决路径改为调用该状态机。
4. `VoiceGateway.from_runtime(...)` 按 `effective_voice_server_mode()` 返回 `OmniVoiceServer` 或 `TextVoiceServer`。
5. `ControlRuntime` 创建 `VoiceGateway`，后续控制入口可以逐步从直接依赖 `VoiceRuntime` 迁移到 server adapter。
6. runtime snapshot 增加 `voice_server_mode`。
7. package-check 的安装导入验证增加：
   - `runtime.voice_gateway`
   - `runtime.omni.omni_voice_server`
   - `runtime.text.text_voice_server`
   - `runtime.text.text_dialog_state_machine`

## 仍保留的兼容

1. `VOICE_REPLY_MODE` 尚未删除，只作为迁移兼容字段。
2. DashScope Omni Realtime 客户端、ASR 和 TTS 类仍在 `VoiceRuntime` 文件内，后续再物理迁移。
3. Omni Server 仍不使用 Text Server 状态机做误触发主裁决；sidecar ASR 只保留日志、回填和低风险辅助。

## 对业务开发者的影响

业务代码不需要修改。推荐配置仍是：

```yaml
voice:
  server_mode: omni_server
```

如果业务明确需要纯文本模型链路，可配置：

```yaml
voice:
  server_mode: text_server
```

## 验证

```bash
uv run --python 3.11 --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_voice_server_boundaries.py \
  openaiglass-sdk/tests/unit/test_settings.py \
  openaiglass-sdk/tests/unit/test_server_cli.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py \
  openaiglass-sdk/tests/unit/test_package_check.py \
  openaiglass-sdk/tests/unit/test_agent_core.py -q
```

结果：通过。
