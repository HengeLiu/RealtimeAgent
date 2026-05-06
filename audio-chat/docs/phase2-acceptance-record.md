# audio-chat 第二阶段补强验收记录

日期：2026-05-06

## 验收范围

本轮把第二阶段从 mock/provider 边界基线补强到“真实 provider 可用、配置完整、端侧设备操作闭环可验证”。仍不进入第三阶段业务迁移、ToolGateway、TaskEngine、Skill Service 或 MCP Gateway 完整落地。

已完成模块：

1. TextAgentCore 内部 `AsrPipeline` provider adapter：`DashScopeAsrProviderAdapter` 使用 `dashscope.audio.asr.Recognition`，支持真实 PCM chunk 输入、transcript delta 和 final transcript。
2. TextAgentCore 内部 `TextModelAdapter`：支持 `mock`、`openai-compatible`、`dashscope-compatible` 流式 text delta。
3. Output Service 内部 Streaming TTS：`DashScopeStreamingTTS` 使用 `dashscope.audio.tts_v2.SpeechSynthesizer`，`assistant_text.delta` 实时进入 TTS，生成 `assistant_audio.delta`。
4. 完整 YAML 配置加载：新增 `audio-chat/examples/minimal/server.yaml` 和 `playback.yaml`，覆盖文档第 15 章的主要配置段，并支持环境变量覆盖。
5. `UserDeviceContext` / `DeviceHandle` 补强：`DeviceHandle.configure_stream()`、`open_stream()`、`start_task()` 和 `EndpointTaskRef.stop()` 已落地；`UserDeviceContext.submit_output()` 由 Context 内部通过 app 门面提交。
6. Asset Service 补强：`get_or_request_asset()` 支持 pending request、timeout、等待端侧上传、TTL、producer device_id 记录和最小 window 缓存。
7. Playback Arbiter 补强：覆盖 queue TTL、同优先级不抢占、interrupt、drop/requeue，以及 queued intent 继续播放路径。
8. 用户打断：`control.user.interrupt.detected` 会 cancel TextAgentCore 当前响应、取消当前 output stream，并写入 Playback Arbiter 决策。
9. 播放回执：playback endpoint 上报 `stream.output.started`、`stream.output.finished`、`stream.output.closed`。
10. ESP32 AEC 试验端侧状态模型：声明 `audio.aec=endpoint`，保留 AEC reference ring buffer、mic send queue、playback ring buffer。

## Provider 配置

mock playback 配置：

```text
audio-chat/examples/minimal/playback.yaml
```

关键字段：

```yaml
runs_root: runs/audio-chat
user_id: user-playback-001
device_id: dev-python-playback-001
asr_provider: mock
asr_model: mock-asr
text_model_provider: mock
text_model: mock-text
tts_provider: mock
tts_model: mock-tts
tts_voice: mock
```

server YAML 配置：

```text
audio-chat/examples/minimal/server.yaml
```

真实 provider adapter 状态：

1. ASR：`dashscope` provider 已接 `dashscope.audio.asr.Recognition`，无 `DASHSCOPE_API_KEY` 时在 `allow_mock_fallback=true` 的开发配置下降级；`allow_mock_fallback=false` 时 fail fast。
2. 文本模型：`openai-compatible` / `dashscope-compatible` 走 OpenAI SDK streaming chat completions。
3. TTS：`dashscope` provider 已接 `dashscope.audio.tts_v2.SpeechSynthesizer`，记录 provider/model/voice/首包指标。

## 测试命令和结果

```bash
uv run python -m pytest audio-chat/tests -q
```

结果：`19 passed`

```bash
git diff --check
```

结果：通过。

```bash
uv run audio-chat.dev.preflight --report runs/audio-chat/preflight-phase2.json
```

结果：通过。

旧概念扫描：

```bash
rg -n "VoiceRuntime|DeviceGroupContext|MediaFrame|group_id|source_device_id|target_device_id" audio-chat/server-python audio-chat/tests audio-chat/examples -S
```

结果：无命中。

## 真实 Provider 可选验收

本机环境存在 `DASHSCOPE_API_KEY`，因此本次 integration 测试实际执行，不是 skip。

```bash
DASHSCOPE_API_KEY=... uv run python -m pytest audio-chat/tests/integration -q
```

结果：`2 passed`

覆盖：

1. `DashScopeAsrProviderAdapter` 可启动真实 Recognition session，接收 PCM chunk 并收尾。
2. `DashScopeStreamingTTS` 可启动真实 SpeechSynthesizer session，接收文本 delta，返回 metrics。

## Playback 回放

命令：

```bash
uv run audio-chat.playback.glass --config audio-chat/examples/minimal/playback.yaml
```

结果：

1. `passed=true`
2. session：`sess_ffd20ebd10e1`
3. `output_chunk_count=3`
4. `output_bytes=3360`
5. 端侧回执包含 `stream.output.started`、`stream.output.finished`、`stream.output.closed`

流式 TTS 顺序检查：

```text
runs/audio-chat/sessions/sess_ffd20ebd10e1/model-events.jsonl
```

`assistant_audio.delta` 出现在最终 `assistant_text.delta final=true` 之前，说明首个 TTS audio delta 没有等待完整文本结束。

## 端侧联调结果

本轮仍未连接物理 ESP32-S3，真实端侧联调用 Python playback endpoint 和 ESP32 AEC 状态模型覆盖协议语义。

已验证：

1. 设备注册成功。
2. 唤醒后打开 `sensor.mic`。
3. server 下发 `actuator.speaker`。
4. endpoint 收到 speaker chunk 后上报 `stream.output.started`。
5. output close request 后 endpoint 上报 `stream.output.finished` 和 `stream.output.closed`。
6. `control.user.interrupt.detected` 可取消当前 output stream，并记录 `cancel_current` 决策。
7. ESP32 AEC 状态模型声明 `audio.aec=endpoint`，并维护 AEC reference ring buffer、mic send queue、playback ring buffer。

未完成：

1. 未连接物理 ESP32-S3 固件。
2. 未验证真实端侧 AEC 算法效果。
3. 未验证真实硬件播放器时钟、reference delay 和播放失败回执。

## sensor.rgb 验收

结果：

1. Tool 通过 `UserDeviceContext.get_or_request_asset(...)` 请求 `sensor.rgb`。
2. Asset Service 缓存未命中后创建 pending request，并发布 `stream.control.configure.requested`。
3. playback endpoint 通过 `sensor.rgb` stream 上传 JPEG mock 样本。
4. Asset Service 等待端侧回传后返回 `AssetRef`。
5. `AssetRef.device_id` 来自 stream producer device_id。
6. Asset Store 支持最小 window 查询和 TTL 过滤。

## 已知问题

1. DashScope integration 当前只做可用性级验证，没有纳入稳定音频样本的语义准确率断言。
2. DashScope TTS adapter 已是真实 streaming session，但同步 `synthesize_delta()` 只等待短窗口内首批音频，后续仍可继续优化成后台队列驱动。
3. Playback Arbiter 的 `requeue` 已能重新打开 output stream，但不实现断点 resume。
4. Asset Service 支持 TTL 和 window，但还没有质量评分、多设备选择策略和连续视频编码。
5. ESP32 仍是 server SDK 中的 endpoint bridge/state model，物理固件接入说明和真机验收仍需继续补。
6. ToolGateway、TaskEngine、Skill Service、MCP Gateway 尚未完整落地。

## 结论

第二阶段补强通过当前验收：真实 DashScope ASR/TTS provider adapter 可用，YAML 配置模型已补齐，Context/DeviceHandle、Asset Service 和 Playback Arbiter 的关键闭环已可测试。下一步仍应先做物理 ESP32 endpoint bridge 和稳定 provider 样本测试，再进入第三阶段业务能力迁移。
