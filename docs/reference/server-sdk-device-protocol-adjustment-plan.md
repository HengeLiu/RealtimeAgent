# Server SDK 与 Device 协议对齐调整计划

本文基于以下四份文档重新对照当前 Server SDK 实现，整理后续需要调整的范围和实施顺序：

- `docs/reference/device-sdk-event-blueprint.md`
- `docs/reference/device-app-integration.md`
- `docs/reference/device-event-behavior.md`
- `protocol/docs/protocol.md`

目标不是重新设计协议，而是让 Server SDK 的真实行为与已经确定的 Device / Server 标准协议一致。本文只定义调整范围和计划，不包含具体代码实现。

## 1. 当前结论

当前真机联调已经证明三条基础链路有不同状态：

| 链路 | 当前状态 | 初步归因 |
| --- | --- | --- |
| 设备注册、心跳、音频会话打开 | 基本可用 | Server SDK 与 Swift Device SDK 已能完成注册、唤醒、`control.audio_session.opened` 和上行麦克风 stream 注册。 |
| RGB 图片上传 | 单帧资产链路可用，但不符合实时视频标准链路 | Server 当前更多按 `asset request -> single frame -> close` 模式运行，和文档中 `stream.control.open.requested(sensor.rgb, continuous)` 后按固定频率维护视频输入链路不完全一致。 |
| speaker 下行音频 | Server 已能生成并发送 chunk，但端侧播放无声仍未定位完成 | Server 已发送 `stream.output.open.requested` 和 `StreamChunk actuator.speaker`；但缺少端侧 `stream.output.started/closed` 回执。Server 侧也存在“写完即 close stream”的行为，与文档要求的“等待端侧 drain 后回执 closed”不完全一致。 |

因此，后续不应该继续零散修补某一处日志，而应该按协议边界把 Server SDK 的事件状态机、实时视觉输入、speaker 输出流控和观测能力系统性对齐。

## 2. 协议对齐原则

Server SDK 需要遵守以下边界：

1. `sensor.mic` 和 `actuator.speaker` 是系统音频主链路，通过 `properties.realtime_agent.audio_input` / `properties.realtime_agent.audio_output` 声明，不作为普通 `supports`。
2. `control.audio_session.opened` 是麦克风上行主链路的 stream 注册点；不再要求额外的 `stream.input.opened(sensor.mic)`。
3. `sensor.rgb` 实时视频应按输入 stream 生命周期维护：`stream.control.open.requested` -> `stream.input.opened` -> 多个 `StreamChunk sensor.rgb` -> `stream.control.close.requested` -> `stream.input.closed`。
4. `actuator.speaker` 是唯一标准 `stream.output.*` 链路；其他业务输出必须走 `custom.*`。
5. `stream.output.finish.requested` / `stream.output.close.requested` 只表示 server 已写完，不表示端侧已播完。Server 不应把 output stream 当作完成态，直到端侧回 `stream.output.closed` / `stream.output.cancelled` / `stream.output.failed`。
6. 端侧 speaker buffer 高低水位线事件 `downstream.pause.requested` / `downstream.resume.requested` 是标准协议事件；Server 必须放行并暂停 / 恢复对该 output stream 的写出。
7. 打断由 Server / provider VAD 判断；Device 只持续上行音频并响应 Server 下发的 `stream.output.cancel.requested`。

## 3. Server SDK 需要调整的范围

### 3.1 协议 schema 和事件白名单

当前已发现的问题：

- 文档已经定义 `downstream.pause.requested` / `downstream.resume.requested`，但协议 schema 和运行时事件枚举曾未放行，导致端侧水位线事件被 Server 当成未知事件。
- 标准协议事件、`custom.*` 扩展事件、内置状态机事件之间需要有统一入口，避免文档允许但运行时拒绝。

调整范围：

1. 对齐 `EventName`、`realtime-agent-event.schema.json`、golden fixtures 和协议测试。
2. 明确 `custom.*` 的 schema 放行策略，避免业务扩展复用 `command.*` 或 `stream.output.*`。
3. 补齐 Server 接收 `downstream.pause.requested` / `downstream.resume.requested` 的协议测试。

验收标准：

- 端侧发送 `downstream.pause.requested` / `downstream.resume.requested` 不再产生 `system.error.raised unknown event_name`。
- 标准事件和 `custom.*` 事件有明确测试覆盖。

### 3.2 设备注册和路由生成

当前已接近目标，但仍需统一复核：

- Server 应从 `properties.realtime_agent.audio_input=sensor.mic` 推导音频输入能力。
- Server 应从 `properties.realtime_agent.audio_output=actuator.speaker` 推导 speaker output 路由。
- `supports.sensors[].type=rgb` 应推导 `sensor.rgb` 输入能力。
- 旧 `routes` / 旧 `capabilities` 不应继续作为主路径。

调整范围：

1. 复核 `device_capabilities.py` 中 routes 生成逻辑是否完全符合文档。
2. 对 `control.device.register.requested` 增加面向新 Device SDK profile 的测试。
3. 确认设备重新注册、断线重连后 frozen consumer / stream producer 不残留旧连接。

验收标准：

- 使用 Device SDK 新注册方式时，不需要手写 routes。
- 注册后 Server 能正确向同一设备投递 `control.audio_session.*`、`stream.control.*(sensor.rgb)` 和 `stream.output.*(actuator.speaker)`。

### 3.3 音频会话和麦克风上行

当前已修正过一个关键问题：`control.audio_session.opened` 需要注册 `sensor.mic` stream，否则上行 chunk 会报 `unknown stream_id`。

仍需整理的范围：

1. 把 `control.audio_session.opened` 注册麦克风 stream 固化为标准行为和测试。
2. `control.audio_session.closed`、心跳超时、control WS 断开时，需要统一关闭或失败化该 device 的 mic stream。
3. 不再要求或鼓励端侧为 `sensor.mic` 发送额外 `stream.input.opened`。
4. `StreamChunk.final` 不应被 Server 理解成一句话结束；turn 边界只来自 Server / provider VAD。

验收标准：

- 设备收到 `control.audio_session.open.requested` 后，只要回 `control.audio_session.opened` 并开始发 `sensor.mic` chunk，Server 就能稳定接收。
- 断线、idle timeout、音频会话关闭时，Server 不再保留可写但无效的 mic stream。

### 3.4 实时视觉输入链路

这是当前和文档差异最大的部分。

文档要求：

- 实时视频输入应是可维护的 stream 链路。
- `stream.control.open.requested(sensor.rgb)` 表示打开视频输入链路，不表示 Server 每次只拍一张照片。
- Device 应按 `frequency_hz` 持续上传帧，直到 Server 下发 `stream.control.close.requested`。

当前实现表现：

- Omni 视觉采样更接近“语音 turn 内按需请求单帧资产”。
- 端侧收到请求后上传一帧并立刻 `stream.input.closed`。
- 真机日志里，图片上传成功，但经常晚于 `omni.response.audio_transcript.delta`，导致模型已经开始回答后才追加视觉帧。

需要调整：

1. Server 在实时对话开始时，应根据设备能力打开 `sensor.rgb` continuous 输入链路，而不是等 provider `speech_started` 后再单帧请求。
2. Server 应在用户 turn 内维护最近一帧或多帧视觉 buffer，保证提交给 Omni / Vision provider 时已有可用图片。
3. `stream.control.open.requested` 的 `mode=continuous`、`frequency_hz`、`format`、`ttl_seconds`、`correlation_id/turn_id` 语义要和协议文档一致。
4. 对 `mode=single` 的资产请求和 `mode=continuous` 的实时视频链路做清晰分层：
   - 工具抓拍、一次性拍照：继续使用 `mode=single`。
   - 实时音视频对话：使用 `mode=continuous`，按固定频率更新视觉 buffer。
5. Provider 侧追加图片时，应优先使用“当前 turn 开始前或 turn 早期已存在的最新视觉帧”，而不是等回答开始后才请求。

验收标准：

- 用户问“你能看到我眼前有什么吗”时，日志中视觉帧追加早于模型开始输出回答。
- 不再出现“图片已上传但模型说看不到画面”的稳定复现。
- `stream.input.opened(sensor.rgb)` / 多帧 `stream.chunk.received(sensor.rgb)` / `stream.input.closed(sensor.rgb)` 顺序可在 runs 中复查。

### 3.5 speaker output 生命周期

文档要求：

- Server 下发 `stream.output.open.requested(actuator.speaker)`。
- Server 通过 stream WS 发送 `StreamChunk actuator.speaker`。
- Device SDK 写入播放 buffer，达到启动水位线后回 `stream.output.started`。
- Server 写完只下发 `stream.output.finish.requested` 或 `stream.output.close.requested`。
- Device 播放 buffer 和本地 speaker sink drain 完成后，回 `stream.output.closed`。
- Server 收到端侧 closed 后，才释放播放仲裁和关闭会话。

当前实现表现：

- Server 已能发送 `stream.output.open.requested` 和 speaker chunks。
- Server 在 `assistant_audio.done` 后立即调用 `stream_service.close_stream()`，内部将 handle 标记为 closed 并下发 `stream.output.finish.requested`。
- 这会让 Server 侧 stream 状态先于端侧回执进入 closed，容易掩盖“端侧到底有没有播放 / 有没有回执”的问题。
- 真机日志中已出现 Server 发了 16 个 speaker chunk，但没有端侧 `stream.output.started` / `stream.output.closed` 回执。

需要调整：

1. 区分 “server finished writing” 和 “output stream closed by endpoint” 两个状态。
2. `assistant_audio.done` 时只下发 `stream.output.finish.requested`，不要立即把 stream registry handle 标成 closed。
3. 等端侧回 `stream.output.closed` / `stream.output.cancelled` / `stream.output.failed` 后，再释放 OutputService 的 active stream、PlaybackArbiter 和会话 pending 状态。
4. `stream.output.cancel.requested` 后同样等待端侧 `stream.output.cancelled` 或 `stream.output.closed`，再终止状态。
5. 如果端侧长期不回执，需要 Server 有超时失败策略，记录 `stream.output.endpoint_ack.timeout` 并释放状态，避免会话泄漏。

验收标准：

- 真机链路中能看到端侧 `stream.output.started` 和最终 `stream.output.closed`。
- Server runs 能区分 `stream.output.finish.requested`、`stream.output.endpoint_closed`、`stream.closed`。
- 无论端侧是否成功播放，Server 都能从回执或超时中得出明确结论。

### 3.6 speaker 下行水位线流控

文档要求：

- 端侧 SDK buffer 到高水位线时发送 `downstream.pause.requested`。
- Server 暂停对该 output stream 的写出，并把后续 provider / TTS 音频暂存在 Server。
- 端侧 SDK buffer 降到低水位线时发送 `downstream.resume.requested`。
- Server 恢复写出暂停期间缓存的音频。

当前实现表现：

- OutputService 已有 `_paused_sessions` 和 `_paused_payload_by_stream` 机制。
- `publish_control_event()` 已有 pause/resume 分支，但调用的是 `agent_core.pause_downstream()` / `resume_downstream()`，需要确认不同 agent core 是否都实现并最终落到 OutputService。
- 目前 pause/resume 以 session 为粒度，协议 payload 以 stream_id 为核心，需要确认多 output stream 或重连场景下不会误伤。

需要调整：

1. Server 接收 pause/resume 时应优先按 `stream_id` 控制具体 output stream，必要时再映射 session。
2. OutputService 的 pause buffer 应记录 stream_id、seq、累计 bytes 和恢复冲刷情况。
3. pause 后 provider 仍可能继续吐 audio delta，Server 必须不再向端侧写出新 chunk，而是缓存。
4. resume 后恢复写出时，seq 必须连续。
5. cancel 优先级高于 pause/resume；cancel 后必须丢弃暂停缓存。

验收标准：

- 构造端侧发送 pause 后，Server 不再向该设备发送 speaker chunk。
- resume 后 Server 继续发送，seq 连续。
- cancel 后缓存被清理，不会在下一轮误播旧音频。

### 3.7 provider VAD、打断和 response 状态

当前已经暴露并修过一个问题：provider 的 `speech_started` 表示用户开始说话，但不一定表示正在打断已有回复。如果没有 active response / active output，不应取消下一轮 response。

需要进一步标准化：

1. Server 只在存在 active response 或 active output 时，把 provider `speech_started` 解释为打断。
2. 普通用户开始说话时，只发布 `audio.speech.started`，不污染 response generation。
3. 打断时应先下发 `stream.output.cancel.requested`，端侧清空播放 buffer 后回执。
4. `omni.response.done status=cancelled` 不能简单等同于失败；需要结合本地是否主动 cancel、是否有音频输出、是否有文本输出记录原因。
5. 对“模型只出 transcript 不出音频”要有明确诊断事件，而不是只看用户有没有听到声音。

验收标准：

- 用户正常提问时不会因为 `speech_started` 预先取消 response。
- 用户在播放中说话时，Server 能下发 output cancel，端侧回执后释放状态。
- runs 中可复查每次 cancel 的发起方和原因。

### 3.8 运行态观测和排障接口

当前排查困难的直接原因之一，是端侧没有足够回执时，Server 只能看到“已发送”，看不到“端侧收到 / 进入 SDK / 写入 speaker sink”。

Server SDK 应补充以下观测点：

1. 每个 output stream 的状态机快照：
   - opened
   - first_chunk_sent
   - finish_requested
   - endpoint_started
   - endpoint_paused
   - endpoint_resumed
   - endpoint_closed
   - endpoint_ack_timeout
2. 每个 visual stream 的状态机快照：
   - open_requested
   - input_opened
   - first_frame_received
   - latest_frame_appended_to_provider
   - close_requested
   - input_closed
3. `/api/debug/playback` 增加端侧回执和水位线状态。
4. `/api/debug/devices` 增加设备注册出来的标准能力和路由摘要。

验收标准：

- 看到“没声音”时，可以从 Server runs 判断卡在：
  - provider 没有 audio delta；
  - Server 没打开 output stream；
  - Server 没发送 chunk；
  - Device 没回 `stream.output.started`；
  - Device started 但没 closed；
  - Device 回 failed / timeout。

## 4. 建议实施阶段

### Phase 1：协议入口和基础状态机收口

目标：先让 Server 不再拒绝文档中的标准事件，并明确音频会话 / output 回执边界。

任务：

1. 固化 `downstream.pause.requested` / `downstream.resume.requested` schema、枚举和测试。
2. 固化 `control.audio_session.opened` 注册 `sensor.mic` stream 的行为测试。
3. 调整 output stream close 语义：Server 写完后只进入 `finish_requested`，等待端侧 closed。
4. 增加 output endpoint ack timeout。
5. 增加 runs 事件，记录 output stream 的 server-side / endpoint-side 状态。

建议测试：

```bash
uv run python -m pytest protocol/protocol-tests/test_protocol_schema_examples.py -q
uv run python -m pytest agent-server/protocol-tests/sdk/runtime/test_audio_session_lifecycle.py -q
uv run python -m pytest agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py -q
uv run python -m pytest agent-server/protocol-tests/sdk/runtime/test_streaming_tts_runtime.py -q
```

### Phase 2：speaker 流控和播放回执闭环

目标：让 Server 能确定端侧是否真的开始播放、是否完成播放、是否因水位线暂停。

任务：

1. 将 pause/resume 从“session 粒度”整理为“stream_id 优先”。
2. pause 期间缓存后续音频，不再写出新 chunk。
3. resume 后按原 seq 继续写出。
4. cancel 时清理暂停缓存和 active stream。
5. `/api/debug/playback` 增加 output stream 状态。

建议测试：

```bash
uv run python -m pytest agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py -q
uv run python -m pytest agent-server/protocol-tests/sdk/runtime/test_phase2_providers_output.py -q
uv run python -m pytest agent-server/protocol-tests/sdk/runtime/test_streaming_tts_runtime.py -q
```

### Phase 3：实时视觉输入链路标准化

目标：把实时音视频对话中的 RGB 输入从“单帧资产请求”调整为“可维护的 continuous input stream”。

任务：

1. 在 audio session 打开后，根据设备能力预打开或按需打开 `sensor.rgb` continuous stream。
2. 维护每个 session 的最新视觉帧 buffer。
3. 用户 turn 提交给 Omni provider 前，优先使用已缓存的最新视觉帧。
4. 保留 `mode=single` 给工具抓拍和一次性资产请求。
5. runs 中区分 single asset request 与 continuous visual stream。

建议测试：

```bash
uv run python -m pytest agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py -q
uv run python -m pytest agent-server/protocol-tests/sdk/runtime/test_stream_and_audio_pipeline.py -q
uv run python -m pytest examples/for-blind-app/app-tests -q
```

### Phase 4：真机联调验收

目标：用 Swift DeviceDemo 验证协议端到端行为。

验收步骤：

1. 重启 Server。
2. 启动 DeviceDemo，点击开始音视频对话。
3. 问：“你能看到我眼前有什么吗？”
4. 检查 Server runs：
   - `control.audio_session.opened`
   - `sensor.mic` chunk received
   - `stream.control.open.requested(sensor.rgb, mode=continuous)` 或明确的 single request
   - `sensor.rgb` frame received
   - visual frame appended before response output
   - `stream.output.open.requested(actuator.speaker)`
   - `stream.chunk.sent(actuator.speaker)`
   - endpoint `stream.output.started`
   - endpoint `stream.output.closed`
5. 手机上能听到声音。

如果失败，按 runs 判断卡点：

| 现象 | 优先排查 |
| --- | --- |
| 没有 `omni.response.audio.delta.decoded` | Provider / Omni response 状态机。 |
| 有 audio delta 但没有 `stream.output.first_chunk.sent` | OutputService / PlaybackArbiter。 |
| 有 chunk sent 但没有 `stream.output.started` | Device SDK stream receive loop 或 Device APP 是否运行最新包。 |
| 有 started 但没有声音 | Device SDK speaker sink / iOS 音频会话。 |
| 有照片存储但模型说看不到 | Server 视觉帧 append 时序。 |

## 5. 实施记录

### Phase 1：协议入口和基础状态机收口

- 状态：已完成。
- 实现：
  - `StreamService` 新增 `request_output_finish()`、`mark_output_endpoint_started()` 和 `mark_output_endpoint_closed()`，区分 server 写完和端侧播放完成。
  - `OutputService` 在 `assistant_audio.done` 后只下发 `stream.output.finish.requested`，保留 active playback，等待端侧 `stream.output.closed/cancelled/failed` 或 ack timeout。
  - 新增 `output.endpoint_ack_timeout_seconds` 默认值和示例配置，维护任务会记录 `stream.output.endpoint_ack.timeout` 并释放状态。
  - `stream.output.started/closed/cancelled/failed` 在 `RealtimeAgentApp.publish_control_event()` 中进入标准回执处理。
- 验证：
  - `uv run python -m pytest protocol/protocol-tests/test_protocol_schema_examples.py agent-server/protocol-tests/sdk/runtime/test_audio_session_lifecycle.py agent-server/protocol-tests/sdk/runtime/test_streaming_tts_runtime.py agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py -q`

### Phase 2：speaker 流控和播放回执闭环

- 状态：已完成。
- 实现：
  - `downstream.pause.requested` / `downstream.resume.requested` 改为优先按 `stream_id` 控制 output stream，缺少 stream_id 时才退回 session 粒度。
  - `OutputRouter` 新增 stream 粒度 pause/resume 状态和暂停缓存，resume 后按原 seq 继续写出。
  - cancel 后只下发 `stream.output.cancel.requested`，等待端侧 `stream.output.cancelled/closed/failed` 或后续 timeout，不再由 Server 自发伪造 `stream.output.cancelled`。
  - `/api/debug/playback` 的 output snapshot 增加 output stream 状态。
- 验证：
  - `uv run python -m pytest agent-server/protocol-tests/sdk/runtime/test_phase2_providers_output.py agent-server/protocol-tests/sdk/runtime/test_stream_and_audio_pipeline.py -q`

### Phase 3：实时视觉输入链路标准化

- 状态：已完成 Server SDK 侧基础链路，待真机验证端侧连续上传节奏。
- 实现：
  - `OmniRealtimeAgentCore` 在 realtime session 打开后请求同一设备的 `sensor.rgb` continuous stream。
  - 视觉线程不再每次通过 `request_asset(mode=single)` 抓拍，而是消费 `AssetService` 中同 session 的最新 `sensor.rgb` asset buffer 并追加到 provider。
  - provider VAD 事件只确保视觉线程运行，不再把视觉 stream 限定为单个 turn；视觉 stream 在音频 session / agent session 关闭时才请求 close。
  - 测试更新为校验 `stream.control.open.requested(mode=continuous)`、paired device 过滤和 continuous buffer append。
- 验证：
  - `uv run python -m pytest agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py agent-server/protocol-tests/sdk/runtime/test_stream_and_audio_pipeline.py examples/for-blind-app/app-tests -q`

### 本次完整回归

```bash
uv run python -m pytest protocol/protocol-tests/test_protocol_schema_examples.py agent-server/protocol-tests/sdk/runtime/test_audio_session_lifecycle.py agent-server/protocol-tests/sdk/runtime/test_streaming_tts_runtime.py agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py agent-server/protocol-tests/sdk/runtime/test_phase2_providers_output.py agent-server/protocol-tests/sdk/runtime/test_stream_and_audio_pipeline.py examples/for-blind-app/app-tests -q
```

结果：通过。

### Phase 4：真机联调验收

- 状态：待人工验收。
- 建议观察点：
  - Server logs / runs 中应先看到 `stream.control.open.requested`，payload `mode=continuous`，再看到连续 `sensor.rgb` asset stored / `omni.visual_frame.appended`。
  - speaker 链路应看到 `stream.output.finish.requested` 后，等待端侧 `stream.output.started` 与 `stream.output.closed`；如果端侧没有回执，Server 会记录 `stream.output.endpoint_ack.timeout`。
  - 端侧无声时优先看是否缺少 `stream.output.started`；如果有 started 但无声，再回到 Swift Device SDK speaker sink 排查。

## 6. 不在本轮 Server SDK 调整中的内容

以下问题需要单独在 Device SDK 或 Device APP 中处理，不应混进 Server SDK 调整：

1. iOS `AVAudioEngine` / `AVAudioPlayerNode` 的真实播放问题。
2. DeviceDemo 是否安装到真机最新包、是否前后台中断。
3. Swift SDK 是否在 `receiveStreamChunk()` 后正确 dispatch `actuator.speaker`。
4. App 调试页是否展示 SDK 的 `lastMediaError`、speaker chunk 计数和 sink 调用计数。

但 Server SDK 需要提供足够观测，让这些端侧问题能被明确分层定位。
