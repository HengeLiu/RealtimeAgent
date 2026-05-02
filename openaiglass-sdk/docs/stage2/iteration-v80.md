# SDK 迭代记录：工具前置播报改为首输出自动判定

对应对外 SDK 版本：`sdk-v80`。

## 背景

工具调用前的等待提示此前由静态配置控制。这个开关会把“是否播报”变成静态配置，但真实语义应当由模型输出决定：

- 如果模型本轮一开始就调用工具，用户还没有收到任何回复，应播放工具配置的静态等待提示。
- 如果模型已经先返回文本或音频，说明用户已经听到或看到反馈，后续再插入等待提示会打断体验。

## 本轮变更

1. `AgentToolContext` 新增本轮首个模型输出类型记录。
2. 工具前置播报不再依赖静态配置：
   - 首输出为 `tool_call`：自动播报工具 `progress_message`。
   - 首输出为 `text` 或 `audio`：工具照常执行，但不播报等待提示。
3. Agent-Core 流式链路会在收到文本增量时记录首输出为 `text`。
4. Chat Completions 音频原生链路会识别首个文本增量或工具调用增量。
5. Omni Realtime 链路会识别首个 `response.audio_transcript.delta`、`response.text.delta`、`response.audio.delta` 或 `response.function_call_arguments.done`。
6. 工具前置播报缓存预加载只要工具配置了播报文案且 TTS 配置可用，就会预生成或加载缓存。
7. 删除旧静态开关，避免配置项继续误导业务开发。

## 验证

1. 单元测试：

```bash
uv run --with pytest python -m pytest \
  openaiglass-sdk/tests/unit/test_agent_core.py \
  openaiglass-sdk/tests/unit/test_voice_runtime.py -q
```

结果：通过。

新增关键用例：

- `test_tool_gateway_suppresses_progress_when_text_is_first_model_output`
- `test_openai_runner_direct_audio_path_suppresses_progress_after_text_delta`
- Omni Realtime 测试补充断言首输出为 `text` 或 `tool_call` 时的识别结果。

2. 设备级回放：

```bash
LOG_LEVEL=DEBUG uv run openaiglass.server.start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env \
  --log-file openaiglass-for-blind/logs/server-auto-progress-check.log

uv run openaiglass.phone.mock \
  --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json

uv run openaiglass.glass.start \
  --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/look_look.json
```

结果：`glass-playback` 返回 `assertions_ok=true`，`actuator_count=2`。

关键日志：

```text
Omni Realtime 工具调用请求 tool_name=manage_memory
工具前置播报命中静态音频缓存 stream_id=reply_675abca14d2e
下行播放请求已发送 stream_id=reply_675abca14d2e audio_source=tts
Omni Realtime 返回首段音频
下行播放请求已发送 stream_id=reply_ea10f2078681 audio_source=omni_realtime
```

验证结束后已停止本地 server 和 phone mock。
