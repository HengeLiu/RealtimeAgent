# audio-chat 第二阶段 / Phase 2.5 验收记录

日期：2026-05-06

## 验收范围

本记录覆盖第二阶段补强和 Phase 2.5 “真实端侧与 Provider 稳定化”。本轮仍不进入第三阶段业务迁移、ToolGateway、TaskEngine、Skill Service 或 MCP Gateway 完整落地。

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

Phase 2.5 新增完成项：

1. Output Service 音频格式协商：`DashScopeStreamingTTS` 和 `MockStreamingTTS` 都暴露 `sample_rate_hz` metrics；Output Router 按 TTS provider 实际输出格式打开 `actuator.speaker` stream，`StreamFormat`、`assistant_audio.delta` chunk 和 metrics 保持一致。DashScope provider 在目标采样率不被 SDK 直接支持时会把 PCM 重采样到配置采样率。
2. TTS 生命周期收紧：Output Router 按 output stream 创建 TTS session，`final` 只关闭当前 stream 绑定的 TTS，不影响其他用户或后续排队输出。
3. DeviceHandle 语义收紧：`DeviceHandle.configure_stream()`、`open_stream()`、`start_task()`、`EndpointTaskRef.stop()` 都作用于 `UserDeviceContext.find_device(...)` 选中的设备；内部按已解析连接定向投递，不在业务事件格式里暴露 `target_device_id`。
4. Asset Service request_id 关联：pending request 生成 request_id 并写入 `stream.control.configure.requested` payload；端侧通过 asset stream 回传时带回 request_id，Asset Service 只完成匹配请求，并使用锁保护 pending 表。
5. Asset TTL 和并发隔离：过期资产不会被缓存命中；两个并发 `sensor.rgb` 请求不会因为上传顺序不同而串包。
6. 稳定 provider 样本：新增脱敏/合成 16 kHz PCM 样本 `audio-chat/testdata/provider/dashscope-nihao-16k.pcm` 和期望文本 `dashscope-nihao-expected.txt`，真实 DashScope ASR integration 会断言 transcript 包含期望片段。

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

结果：`24 passed`

```bash
git diff --check
```

结果：通过。

```bash
uv run audio-chat.dev.preflight --report runs/audio-chat/preflight-phase25.json
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
DASHSCOPE_API_KEY=... uv run python -m pytest audio-chat/tests/integration -q -rs
```

结果：`2 passed`

覆盖：

1. `DashScopeAsrProviderAdapter` 可启动真实 Recognition session，接收 16 kHz PCM 样本，并断言 final transcript 包含 `audio-chat/testdata/provider/dashscope-nihao-expected.txt` 中的期望片段。
2. `DashScopeStreamingTTS` 可启动真实 SpeechSynthesizer session，按配置返回非空 PCM，metrics 包含 provider、model、voice、`sample_rate_hz=16000` 和 `tts_first_audio_latency_ms`。
3. 无 `DASHSCOPE_API_KEY` 时 integration 测试会 skip，不会用 mock 冒充真实 provider 验收。

## Playback 回放

命令：

```bash
uv run audio-chat.playback.glass --config audio-chat/examples/minimal/playback.yaml
```

结果：

1. `passed=true`
2. session：`sess_0bcf0f985cf8`
3. `output_chunk_count=3`
4. `output_bytes=3360`
5. 端侧回执包含 `stream.output.started`、`stream.output.finished`、`stream.output.closed`

流式 TTS 顺序检查：

```text
runs/audio-chat/sessions/sess_0bcf0f985cf8/model-events.jsonl
```

`assistant_audio.delta` 出现在最终 `assistant_text.delta final=true` 之前，说明首个 TTS audio delta 没有等待完整文本结束。

Phase 2.5 格式协商检查：

1. 单元测试覆盖 TTS provider metrics、`StreamFormat` 和 output chunk `sample_rate` 一致。
2. Playback mock 配置仍使用 16 kHz，真实 DashScope TTS integration 使用 16 kHz 配置并断言 metrics 一致。
3. 如果后续端侧声明 22050 Hz 播放能力，Output Router 会用 provider metrics 打开 22050 Hz `actuator.speaker` stream；如果配置要求 16000 Hz，provider adapter 会产出或重采样为 16000 Hz。

## 端侧联调结果

本轮仍未连接物理 ESP32-S3，真实端侧联调用 Python playback endpoint 覆盖协议语义，并补充了 ESP32-S3 endpoint bridge 联调说明：`audio-chat/docs/esp32-s3-endpoint-bridge.md`。

已验证：

1. 设备注册成功。
2. 唤醒后打开 `sensor.mic`。
3. server 下发 `actuator.speaker`。
4. endpoint 收到 speaker chunk 后上报 `stream.output.started`。
5. output close request 后 endpoint 上报 `stream.output.finished` 和 `stream.output.closed`。
6. `control.user.interrupt.detected` 可取消当前 output stream，并记录 `cancel_current` 决策。
7. ESP32 AEC 试验代码中已有 reference ring buffer、mic send queue、playback ring buffer 的写入时机：server 下发 speaker audio 后，端侧写入播放缓冲，并在实际写 I2S 时同步写入 AEC reference。

未完成：

1. 未连接物理 ESP32-S3 固件，因此没有真实注册事件、wake 事件、`sensor.mic` chunk、`actuator.speaker` started/finished/closed 和 AEC reference 统计的真机日志。
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

1. 物理 ESP32-S3 endpoint bridge 尚未真机验收；当前只有 bridge 联调说明和旧 AEC 试验链路代码证据。
2. DashScope TTS adapter 已是真实 streaming session，但同步 `synthesize_delta()` 仍是短窗口取音频，后续可优化成后台队列驱动以降低首包抖动。
3. Playback Arbiter 的 `requeue` 已能重新打开 output stream，但不实现断点 resume。
4. Asset Service 支持 TTL 和 window，但还没有质量评分、多设备选择策略和连续视频编码。
5. ESP32 真机还需要按 `audio-chat/docs/esp32-s3-endpoint-bridge.md` 补充实际刷写命令、串口日志、server 日志和 runs 产物。
6. ToolGateway、TaskEngine、Skill Service、MCP Gateway 尚未完整落地。

## 结论

Phase 2.5 的 provider、stream 格式、DeviceHandle、Asset request_id、playback 和 preflight 验收通过。真实 DashScope ASR/TTS 已可用，真实输出音频格式协商已落地；物理 ESP32-S3 endpoint bridge 仍是阻塞项，不能描述为已真机完成。下一步应先完成 ESP32 真机验收，再进入第三阶段业务能力迁移。
