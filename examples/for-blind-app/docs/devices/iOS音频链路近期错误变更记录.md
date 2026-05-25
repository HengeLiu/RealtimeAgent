# iOS 音频链路近期错误变更记录

日期：2026-05-26

本文档记录近期为了修复 iOS 真机听不到声音、播放不稳定、回声触发打断等问题而做过的关键变更，以及这些变更中已经暴露为错误方向或高度可疑的点。本文档的目的不是证明这些方案可用，而是为后续回退和重写保留事实边界。

## 当前现象

近期多次真机联调表现不一致：

- 有时服务端生成并发送了音频，但 App 端听不到声音。
- 有时第一句话没有声音，第二句话能听到声音。
- 有时第一句话能听到声音，后续提问没有反应。
- 服务端日志中出现过 `omni.response.message_suppressed_after_interrupt`、`omni.response.done_ignored_after_interrupt`、`stream.closed reason=endpoint_endpoint_ack_timeout`、`downstream.pause.requested` 等事件。

从日志看，问题不是单一播放失败，而是至少混合了三类异常：

- provider 侧把输入误判为打断，导致 response 被取消或抑制。
- 服务端已经发送 output stream，但端侧没有稳定回报 `stream.output.closed` 或 `stream.output.failed`。
- 端侧播放、麦克风采集、回声抑制和下行流控之间存在竞争或顺序不确定。

## 当前修改范围

当前工作区涉及以下文件，回退时需要整体检查，不能只回退一个文件：

```text
agent-server/realtime_agent/agent_core/omni.py
agent-server/realtime_agent/app.py
agent-server/realtime_agent/control/service.py
agent-server/realtime_agent/output/service.py
agent-server/realtime_agent/server.py
agent-server/realtime_agent/stream/service.py
agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py
agent-server/protocol-tests/sdk/runtime/test_device_event_behavior_standard.py
agent-server/protocol-tests/sdk/runtime/test_stream_and_audio_pipeline.py
agent-server/protocol-tests/sdk/runtime/test_streaming_tts_runtime.py
devices/swift/Sources/RealtimeAgentDeviceKit/Media/AVFoundationAdapters.swift
devices/swift/Sources/RealtimeAgentDeviceKit/Media/CameraFrameSource.swift
devices/swift/Sources/RealtimeAgentDeviceKit/Media/SpeakerPlaybackBuffer.swift
devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentDeviceClient.swift
devices/swift/Tests/RealtimeAgentDeviceKitTests/RealtimeAgentDeviceKitTests.swift
examples/for-blind-app/agent-server/server.yaml
examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/ContentView.swift
examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/Core/RealtimeAgentEndpointRuntime.swift
examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/Resources/AppConfig.json
```

## 已明确错误或高度可疑的变更点

### 1. 把回声抑制作为临时补丁直接塞进播放/录音路径

涉及文件：

- `devices/swift/Sources/RealtimeAgentDeviceKit/Media/AVFoundationAdapters.swift`

近期改动：

- 麦克风启动时调用 `input.setVoiceProcessingEnabled(true)`。
- 扬声器 `prepare()` 时把 iOS session 保持在 `.playAndRecord + .voiceChat`。
- 播放阶段多次 `setActive(true)` 和 `overrideOutputAudioPort(.speaker)`。

问题判断：

- 回声抑制确实应该处理，但不应该作为局部补丁散落在麦克风和扬声器类里。
- `AVAudioSession` 是全局状态，麦克风和扬声器分别改 category/mode 容易互相覆盖。
- 这类变更必须通过真机验证播放路由、采集路由、外放音量、蓝牙路由、前后台切换；仅靠 Swift 单元测试不能证明可用。
- 当前做法把 AEC、播放路由和播放器启动混在一起，导致问题更难定位。

回退建议：

- 回退这部分补丁。
- 重写时先设计一个独立的 iOS 音频会话管理器，由它统一设置 `AVAudioSession`，不要让麦克风和扬声器各自抢全局 session。

### 2. 服务端下行 pause/resume 和端侧播放 buffer 同时控制流量

涉及文件：

- `agent-server/realtime_agent/app.py`
- `agent-server/realtime_agent/control/service.py`
- `agent-server/realtime_agent/output/service.py`
- `agent-server/realtime_agent/server.py`
- `devices/swift/Sources/RealtimeAgentDeviceKit/Media/SpeakerPlaybackBuffer.swift`
- `devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentDeviceClient.swift`

近期改动：

- 端侧 buffer high watermark 上报 `downstream.pause.requested`。
- 服务端收到 pause 后暂停 stream WebSocket 尚未发送的 speaker chunk。
- resume 后再把保留的 chunk 冲刷出去。
- 服务端增加 `stream.output.endpoint_pause.timeout` 超时清理。

问题判断：

- 这相当于同时在端侧和服务端做播放缓冲控制，状态被拆到两端，且依赖 control WebSocket 和 stream WebSocket 的跨通道顺序。
- 如果端侧没有稳定发送 resume，服务端会长期持有 chunk；如果服务端 finish 又在 control 通道提前到达，端侧可能等待 last seq 或 drain，最终变成 `endpoint_ack_timeout`。
- 该方案引入了新的竞争态，没有先证明原始问题是“server 发送太快导致端侧 buffer 爆掉”。

回退建议：

- 回退服务端 `pause_stream_delivery/resume_stream_delivery/clear_stream_delivery` 及相关 hold queue。
- 先恢复到简单、可观察的顺序：服务端按原逻辑发送，端侧只负责播放和明确 ACK。

### 3. 在 output finish 事件里新增 `last_seq/chunk_count/payload_size`

涉及文件：

- `agent-server/realtime_agent/stream/service.py`
- `devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentDeviceClient.swift`
- 相关协议测试文件

近期改动：

- 服务端在 `stream.output.finish.requested` / `stream.output.close.requested` payload 中加入 `last_seq`、`chunk_count`、`payload_size`。
- Swift SDK 收到 finish 后，如果还没收到 `last_seq`，就暂存 pending finish，等最后一个 chunk 到达后再 drain 和回执 closed。

问题判断：

- 这个方向试图解决 control 通道 finish 早于 stream chunk 的问题，但实际引入了新的等待条件。
- 如果 chunk 丢失、seq 统计错误、端侧没有记录到 chunk，端侧会一直 pending，服务端最后只能 `endpoint_ack_timeout`。
- 这是协议层行为变化，不能在真机播放事故中临时加入并和其他变更混跑。

回退建议：

- 回退 finish payload 扩展和端侧 pending finish 逻辑。
- 重写时如果需要解决跨通道顺序问题，应作为明确协议修订，先写标准、状态机和兼容策略，再实现。

### 4. 播放 drain 增加强制完成逻辑

涉及文件：

- `devices/swift/Sources/RealtimeAgentDeviceKit/Media/AVFoundationAdapters.swift`

近期改动：

- `drain()` 根据 pending duration 设置超时。
- 超时后 `forceFinishPendingPlayback()` 清空 pending buffer 并释放等待。

问题判断：

- 该逻辑可能掩盖真实播放失败，也可能在音频未真实播放完时提前回 `closed`。
- 它不能解释“没有声音”本身，只会改变端侧回执时机。
- 在当前问题里，强制 drain 会让播放成功、播放失败、播放被取消三种状态更难区分。

回退建议：

- 回退强制完成逻辑。
- 重写时需要明确区分：chunk 已写入 AVAudioEngine、AVAudioPlayerNode 已开始、buffer completion 回调完成、用户实际可听这几个不同层级。

### 5. Omni 会话按新 mic stream 强制重建 provider

涉及文件：

- `agent-server/realtime_agent/agent_core/omni.py`
- `agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py`

近期改动：

- 如果同一个 `session_id` 出现新的 `sensor.mic` stream id，就关闭旧 provider，清理 active response 和 interrupted state。

问题判断：

- 该改动针对“App 重启后旧 response 状态残留”这个现象，但判断条件过粗。
- mic stream id 变化不一定等于用户开启了全新对话，也可能是端侧重连、网络恢复或正常 stream rollover。
- 在实时音频链路里强行 close provider 可能导致正在处理的输入或输出被截断。

回退建议：

- 回退这个启发式 provider 重建逻辑。
- 重写时要以明确的 audio session lifecycle 事件作为边界，例如 `control.audio_session.opened/closed`，不要只靠 stream id 变化推断。

### 6. 视觉帧限流与音频问题混在同一轮修改中

涉及文件：

- `agent-server/realtime_agent/agent_core/omni.py`
- `examples/for-blind-app/agent-server/server.yaml`
- `agent-server/protocol-tests/sdk/agent_core/test_omni_agent_core.py`

近期改动：

- 增加单轮视觉帧 append 限制。
- 把示例配置 `max_frames_per_turn` 从 8 改成 1。
- 在 speech started 时尝试追加一帧视觉帧，并在 response active 时跳过。

问题判断：

- 视觉帧节流可能是另一个独立问题，但不应该和“iOS 听不到声音”同批修改。
- 它会改变模型输入内容和时序，让音频问题排查结果变得不纯。
- 如果模型 response 行为变了，无法判断是音频播放修复、视觉输入变化，还是 provider 行为变化导致。

回退建议：

- 回退示例配置和视觉帧相关行为修改。
- 如果确实要处理视觉帧，应单独开任务，用独立回放和 provider 日志验证。

### 7. Camera continuous stream 和 close ACK 改动不应混入音频修复

涉及文件：

- `devices/swift/Sources/RealtimeAgentDeviceKit/Media/CameraFrameSource.swift`
- `devices/swift/Sources/RealtimeAgentDeviceKit/RealtimeAgentDeviceClient.swift`

近期改动：

- 增加 `onStreamClose("sensor.rgb")`。
- continuous camera stream 改成 open-ended task。
- close 时取消 continuous task 并回 `stream.input.closed`。

问题判断：

- 这是视觉流生命周期问题，不是 iOS 播放无声的直接修复。
- 混入这类改动会扩大回退范围，也会让真机联调时的 CPU、网络、摄像头行为同时变化。

回退建议：

- 作为音频链路回退的一部分先撤掉。
- 后续如果需要 continuous camera，再单独设计和测试。

### 8. iOS App 配置结构和诊断 UI 与音频修复耦合

涉及文件：

- `examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/ContentView.swift`
- `examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/Core/RealtimeAgentEndpointRuntime.swift`
- `examples/for-blind-app/devices/native-ios-phone/RealtimeAgentPhone/Resources/AppConfig.json`

近期改动：

- UI 增加 speaker chunk 和 SDK diagnostics。
- runtime 增加 `Logger`、`print`、diagnostics polling。
- registration properties 增加 `audio.aec=ios_voice_processing`、`audio.session_mode=voiceChat`。
- `AppConfig.json` 从能力声明结构改成运行配置结构。

问题判断：

- 诊断 UI 和日志本身有价值，但不应该和播放链路行为修改绑在一起。
- `AppConfig.json` 结构变化可能影响注册能力和运行行为，不能作为音频修复的隐性前提。
- `audio.aec` 属性只能说明“代码声称启用了 AEC”，不能证明真机系统已经稳定进入语音处理模式。

回退建议：

- 回退配置结构变化。
- 诊断 UI 可以在重写时单独保留或重做，但必须只做观测，不改变协议和播放路径。

### 9. 测试通过给了错误信心

涉及文件：

- `agent-server/protocol-tests/sdk/...`
- `devices/swift/Tests/RealtimeAgentDeviceKitTests/...`

近期改动：

- 增加了大量协议测试、Swift 单元测试和 fake provider 测试。

问题判断：

- 这些测试主要证明局部状态机和 mock 行为，没有覆盖真机 AVAudioSession、AVAudioEngine、系统回声抑制、控制/stream 双 WebSocket 真实顺序。
- 在没有真机验证前，不能把这些测试通过解释为“播放链路已修复”。
- 当前故障的核心恰恰在跨设备、跨通道、系统音频会话和 provider VAD 的组合行为，mock 测试覆盖不足。

回退建议：

- 伴随实现一起回退相关测试，避免测试继续固化错误设计。
- 重写时先补真实联调观察点，再补针对真实问题的最小测试。

## 建议的回退边界

建议下一步以“恢复可理解的最小链路”为目标，而不是继续在当前状态上补丁：

1. 回退服务端 output pause/resume、held chunk、finish last seq、ack timeout 扩展。
2. 回退 Swift SDK pending finish、speaker buffer 大重构、强制 drain、disabled speaker 失败回报等同批变更。
3. 回退 iOS AEC 临时补丁，后续按独立音频会话管理器重写。
4. 回退 Omni 按 mic stream 替换 provider 的启发式逻辑。
5. 回退视觉帧限流和 camera continuous stream 生命周期改动。
6. 保留本文档，不保留会混淆真实链路的临时代码。

## 重写原则

后续重写应按以下顺序推进：

1. 先只做观测，不改行为：端侧记录 `AVAudioSession` category/mode/route、播放器启动、chunk seq、closed/failed 回执、麦克风 speech 触发时间。
2. 明确播放生命周期唯一 owner：端侧负责真实播放完成判定，服务端只等待明确 ACK，不再同时做端侧 buffer 和网络发送流控。
3. 明确音频会话 owner：iOS 只允许一个模块统一设置 `AVAudioSession`，麦克风和扬声器不能各自修改全局 session。
4. 明确打断策略：用户真实说话和扬声器回声必须从端侧音频处理、provider VAD、server interrupt 规则三层分别验证。
5. 每次只改一层：先保证“生成的音频一定能播并 ACK”，再处理回声抑制，再处理视觉帧和多模态输入。
6. 真机验收优先于 mock 测试：每个修复都要有 server runs、iOS App 日志、用户可听结果三类证据。

## 下一轮最小验收标准

重写后的第一阶段只验证以下内容：

- 第一次提问必须有声音。
- 连续第二次提问必须有声音。
- 服务端 output stream 最终必须收到端侧 `stream.output.closed` 或 `stream.output.failed`，不能是 `endpoint_ack_timeout`。
- 助手播放期间不能因为自身外放稳定触发 `provider_speech_started`。
- 端侧日志必须能看到每个 output stream 的 open、chunk seq、播放开始、播放 drain、closed/failed。
