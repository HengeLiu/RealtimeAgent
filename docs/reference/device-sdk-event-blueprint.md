# 端侧 SDK 事件行为实现蓝图

本文给出各语言端侧 SDK 的统一实现蓝图。目标是让浏览器、Python、Swift、Kotlin、C/C++、嵌入式网关等不同端侧实现，按同一套事件行为支持三大功能：

1. 设备注册。
2. 开启实时对话。
3. 设备消费 server 事件。

本文只写时序图和伪代码，不写任何具体语言实现。各语言 SDK 后续应把这些伪代码翻译成符合本语言习惯的 API、状态机和测试。

## 1. SDK 分层

端侧 SDK 负责通讯、事件、stream、状态机、回执和平台默认硬件 adapter。麦克风、相机、喇叭默认禁用；App 显式 enable 后，SDK 才使用平台默认 adapter 注册这些能力。App 可以覆盖默认 adapter，但不应该被迫手写 chunk、WebSocket 或标准事件状态机。

```plantuml
@startuml
skinparam componentStyle rectangle

package "Device App" {
  [Mic Adapter]
  [Vision Adapter]
  [Playback Adapter]
  [Command Handlers]
}

package "Device SDK" {
  [Device Profile]
  [Control Channel]
  [Audio Input Channel]
  [Audio Output Channel]
  [Visual Input Channel]
  [Event Router]
  [Registration Manager]
  [Realtime AV Session]
  [Server Event Consumers]
  [Heartbeat Manager]
}

package "Server Boundary" {
  [Control WebSocket]
  [Audio Input WebSocket]
  [Audio Output WebSocket]
  [Visual Input WebSocket]
}

[Mic Adapter] --> [Realtime AV Session]
[Vision Adapter] --> [Realtime AV Session]
[Playback Adapter] --> [Server Event Consumers]
[Command Handlers] --> [Server Event Consumers]

[Device Profile] --> [Registration Manager]
[Registration Manager] --> [Control Channel]
[Heartbeat Manager] --> [Control Channel]
[Event Router] --> [Realtime AV Session]
[Event Router] --> [Server Event Consumers]
[Realtime AV Session] --> [Audio Input Channel]
[Realtime AV Session] --> [Visual Input Channel]
[Server Event Consumers] --> [Audio Output Channel]

[Control Channel] --> [Control WebSocket]
[Audio Input Channel] --> [Audio Input WebSocket]
[Audio Output Channel] --> [Audio Output WebSocket]
[Visual Input Channel] --> [Visual Input WebSocket]
@enduml
```

SDK 对外需要暴露的抽象能力：

| 抽象 | 职责 |
| --- | --- |
| Device Profile | 生成注册 payload，包含身份、runtime、properties、supports。 |
| Control Channel | 连接 `/ws/control`，发送和接收 JSON Event。 |
| Audio Input Channel | 连接 `/ws/stream/audio/input?device_id=...`，发送 `sensor.mic` StreamChunk。 |
| Audio Output Channel | 连接 `/ws/stream/audio/output?device_id=...`，接收 `actuator.speaker` StreamChunk。 |
| Visual Input Channel | 连接 `/ws/stream/visual/input?device_id=...`，在 server 请求后发送 `sensor.rgb` 单帧图片 StreamChunk。 |
| Event Router | 按事件名和 stream_type 分发 server 事件。 |
| Registration Manager | 管理注册、注册失败、重连和心跳启动。 |
| Realtime AV Session | 管理唤醒、音频会话、麦克风流、视觉单帧采集和 speaker 播放。 |
| Server Event Consumers | 处理 `stream.control.*`、speaker 专用 `stream.output.*`、`downstream.*`、`custom.*` 并发送回执。 |
| Heartbeat Manager | 注册成功后按 server 返回间隔发送心跳。 |

### 1.1 本地硬件绑定约定

SDK 必须提供“默认禁用、显式启用、可覆盖 adapter”的硬件接入 API。默认情况下不注册麦克风、相机或喇叭；App 显式 enable 后，SDK 使用平台默认 adapter。默认 adapter 不适用时，App 可以覆盖 source / sink。会话过程中 SDK 只使用已启用或已覆盖的 adapter，不要求 App 处理协议 chunk。

| 绑定对象 | 方向 | SDK 使用方式 | 最小能力 |
| --- | --- | --- | --- |
| `sensor.mic` source | SDK/App -> SDK -> Server | 显式启用后，SDK 从默认或覆盖 source 读取 PCM 字节，封装 `StreamChunk sensor.mic` 写入音频上行链路 | `format`、`readChunk()` 或 async chunk producer、`close()` |
| `sensor.rgb` source | SDK/App -> SDK -> Server | 显式启用后，SDK 只在收到 server 单帧采集请求时读取一帧，封装 `StreamChunk sensor.rgb` 写入视觉上行链路 | `format`、`readFrame()`、`close()` |
| `actuator.speaker` sink | Server -> SDK -> SDK/App | 显式启用后，SDK 从音频下行链路接收 `StreamChunk actuator.speaker`，经过 SDK playback buffer 后写入默认或覆盖 sink 播放 | `prepare(format)`、`writeChunk()`、`drain()`、`cancel()`、`close()` |
| 自定义业务动作 | Server -> SDK -> App | SDK 通过 `custom.command.*` 或其他 `custom.*` 调用 App 注册的 handler | `on_custom_command(...)` 或 `on_event(...)` |

目标 API 形态示例：

```text
sdk.enableAudioInput()
sdk.enableCamera()
sdk.enableSpeaker(buffer = PlaybackBuffer.default())

sdk.overrideInput("sensor.mic", customMicSource)
sdk.overrideInput("sensor.rgb", customCameraSource)
sdk.overrideOutput("actuator.speaker", customSpeakerSink)
```

`customMicSource`、`customCameraSource`、`customSpeakerSink` 可以包装真实硬件、浏览器媒体流、文件样例或测试 mock。SDK 不关心资源来自哪里，只要求 adapter 的格式声明和实际字节一致。

speaker sink 不负责实现端侧接收 buffer，也不负责判断水位线。speaker 播放 buffer 是 SDK 的内置能力，用来隐藏网络抖动并控制内存占用。App 开发者只需要配置 SDK 的播放 buffer 参数；SDK 用这些参数决定何时向 server 发送 `downstream.pause.requested` / `downstream.resume.requested`。

```text
sdk.configurePlaybackBuffer("actuator.speaker", {
  start_watermark_ms: 120,
  low_watermark_ms: 300,
  high_watermark_ms: 800,
  max_buffer_ms: 1200
})
```

## 2. 设备注册

### 2.1 时序图

```plantuml
@startuml
participant "Device App" as App
participant "Device SDK" as SDK
participant Server

App -> SDK: create device profile
App -> SDK: connect()
SDK -> Server: open /ws/control
SDK -> Server: control.device.register.requested
alt registered
  Server -> SDK: control.device.registered
  SDK -> App: onRegistered()
  loop heartbeat interval
    SDK -> Server: control.device.heartbeat.received
  end
else register failed
  Server -> SDK: control.device.register.failed
  SDK -> App: onRegisterFailed(reason)
end
@enduml
```

### 2.2 伪代码

```text
TYPE DeviceProfile:
  device_id
  user_id
  name
  client_type
  sdk_version
  runtime
  properties
  supports

FUNCTION buildRegistrationEvent(profile):
  REQUIRE profile.device_id is not empty
  REQUIRE profile.user_id is not empty
  REQUIRE profile.supports uses structured sensors/actuators
  REQUIRE generated payload has no routes
  REQUIRE generated payload has no legacy capabilities

  RETURN Event(
    event_name = "control.device.register.requested",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    payload = {
      device_id = profile.device_id,
      name = profile.name,
      client_type = profile.client_type,
      sdk_version = profile.sdk_version,
      runtime = profile.runtime,
      properties = profile.properties,
      supports = profile.supports
    }
  )

FUNCTION connectAndRegister(profile):
  control.open("/ws/control")
  registration_event = buildRegistrationEvent(profile)
  control.send(registration_event)

  LOOP while control is open:
    event = control.receive()
    IF event.event_name == "control.device.registered":
      state.registered = true
      state.connection_id = event.payload.connection_id
      heartbeat.start(event.payload.heartbeat_interval_seconds)
      callbacks.onRegistered(event)
      eventRouter.start()
    ELSE IF event.event_name == "control.device.register.failed":
      state.registered = false
      callbacks.onRegisterFailed(event.payload.reason)
      STOP registration flow
    ELSE:
      eventRouter.dispatch(event)

FUNCTION heartbeatLoop(interval_seconds):
  LOOP while state.registered and control is open:
    sleep(interval_seconds)
    control.send(Event(
      event_name = "control.device.heartbeat.received",
      user_id = profile.user_id,
      producer_id = profile.device_id,
      payload = {
        connection_state = "online",
        client_type = profile.client_type
      }
    ))
```

## 3. 开启实时对话

实时对话由唤醒事件触发。端侧 SDK 不做语音起止判断，VAD / turn 边界由 server 根据连续 `sensor.mic` 音频流判断。麦克风硬件或系统录音资源可以由 SDK 默认 adapter 管理，也可以由 App 覆盖 adapter；SDK 的协议责任是在收到 `control.audio_session.open.requested` 后建立或复用音频上行、音频下行和按需视觉上行链路，发送 `control.audio_session.opened`，并在会话打开后维护麦克风上行、server 请求触发的视觉单帧采集和 server 音频下行播放。

麦克风上行的准备完成标记使用会话级 `control.audio_session.opened`，不再额外发送 `stream.input.opened (sensor.mic)`。端侧必须先确认音频上行链路可写、`sensor.mic` source 可读，再发送 `control.audio_session.opened`；server 只有在收到该回执后，才能把本轮实时对话视为可用并消费后续 `sensor.mic` chunk。

### 3.1 时序图

```plantuml
@startuml
participant "Device App" as App
participant "Device SDK" as SDK
participant Server

App -> SDK: wake()
SDK -> Server: control.user.wake.detected
Server -> SDK: control.audio_session.open.requested
SDK -> App: ensure mic source is bound and readable
SDK -> Server: open /ws/stream/audio/input?device_id=...
SDK -> Server: open /ws/stream/audio/output?device_id=...
SDK -> App: prepare or reuse session speaker sink
SDK -> Server: control.audio_session.opened
loop mic chunks
  SDK -> App: read mic source
  App -> SDK: mic pcm chunk
  SDK -> Server: StreamChunk sensor.mic over audio input link
end

opt visual input requested
  Server -> SDK: stream.control.open.requested (sensor.rgb, mode=single, sample_count=1)
  SDK -> Server: open /ws/stream/visual/input?device_id=...
  SDK -> App: captureOneCameraOrVisualSampleFrame(request)
  SDK -> Server: stream.input.opened (sensor.rgb)
  App -> SDK: image frame
  SDK -> Server: StreamChunk sensor.rgb final=true over visual input link
  SDK -> Server: stream.input.closed (sensor.rgb)
end

opt assistant audio output
  Server -> SDK: stream.output.open.requested (actuator.speaker)
  SDK -> SDK: reset per-output playback state
  SDK -> App: reuse session speaker sink
  SDK -> Server: stream.output.ready
  Server -> SDK: StreamChunk actuator.speaker over audio output link
  SDK -> SDK: enqueue SDK playback buffer
  SDK -> App: write SDK playback buffer to bound speaker sink
  SDK -> Server: stream.output.started
  alt output cancel requested
    Server -> SDK: stream.output.cancel.requested
    SDK -> SDK: clear SDK playback buffer
    SDK -> App: stop speaker sink
    SDK -> Server: stream.output.closed or stream.output.cancelled
  else response audio completed
    Server -> SDK: stream.output.close.requested
    SDK -> SDK: drain SDK playback buffer
    SDK -> App: drain speaker sink
    SDK -> Server: stream.output.closed
  end
end

Server -> SDK: control.audio_session.close.requested
SDK -> App: waitPlaybackDrain()
SDK -> Server: control.audio_session.closed
@enduml
```

### 3.2 音频下行播放与 SDK 水位线流控时序图

音频下行播放从主实时对话图中拆出来单独说明。这里的 buffer 是端侧 SDK 的内置播放 buffer，不是 App 或 speaker sink 的自定义队列。App 只提供 `actuator.speaker` sink，并配置 SDK buffer 的启动水位线、低水位线、高水位线和最大容量。

音频下行物理 WebSocket 和 session 级 speaker sink/runtime 已经在 `control.audio_session.opened` 之前建立或准备完成。`stream.output.open.requested` 不表示重新打开 `/ws/stream/audio/output`，也不要求每轮重建播放器；它只要求端侧在这条已建立的音频下行链路上重置本轮逻辑 output stream 状态，例如 `stream_id`、seq 计数、start/close/cancel 标记、上轮残留 buffer 和水位线状态。这个 open/requested 和 ready 握手的粒度是每轮 assistant 回复一次，不是每个音频 chunk 一次。server 下发 `stream.output.open.requested` 后必须等待端侧回 `stream.output.ready`，确认本轮逻辑状态已经干净可写，再向音频下行链路写入第一包 `actuator.speaker` chunk；同一轮回复的后续 chunk 直接走已建立的音频下行链路，不再重复 open/requested 和 ready。`stream.output.started` 只表示端侧达到起播水位并开始向 speaker sink 写出，不表示准备完成。

```plantuml
@startuml
participant "Device App" as App
participant "Device SDK" as SDK
participant Server

Server -> SDK: stream.output.open.requested (actuator.speaker)
SDK -> SDK: reset per-output playback state
SDK -> App: reuse session speaker sink
SDK -> Server: stream.output.ready
loop downstream audio chunks
  Server -> SDK: StreamChunk actuator.speaker over audio output link
  SDK -> SDK: enqueue SDK playback buffer
  opt buffer reaches start watermark
    SDK -> SDK: start playback drain loop
    SDK -> App: write buffered chunks to speaker sink
    SDK -> Server: stream.output.started
  end
  opt buffer reaches high watermark
    SDK -> Server: downstream.pause.requested (buffered_ms, high_watermark_ms)
    Server -> Server: pause audio output link writes and buffer output payload
  end
  opt buffer drains to low watermark
    SDK -> Server: downstream.resume.requested (buffered_ms, low_watermark_ms)
    Server -> SDK: buffered StreamChunk actuator.speaker over audio output link
  end
end
alt output cancel requested
  Server -> SDK: stream.output.cancel.requested
  SDK -> SDK: clear SDK playback buffer
  SDK -> App: stop speaker sink
  SDK -> Server: stream.output.closed or stream.output.cancelled
else response audio completed
  Server -> SDK: stream.output.close.requested or stream.output.finish.requested
  SDK -> SDK: wait output_last_seq chunk if provided
  SDK -> SDK: drain SDK playback buffer
  SDK -> App: drain speaker sink
  SDK -> Server: stream.output.closed
end
@enduml
```

### 3.3 伪代码

```text
TYPE RealtimeAVSession:
  session_id
  mic_stream_id
  mic_state
  visual_stream_id
  visual_state
  audio_input_channel_state
  audio_output_channel_state
  visual_input_channel_state
  playback_state

FUNCTION wake(wake_source):
  REQUIRE state.registered == true
  control.send(Event(
    event_name = "control.user.wake.detected",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    payload = { wake_source }
  ))

FUNCTION handleAudioSessionOpenRequested(event):
  IF realtime.mic_state is "open":
    RETURN

  realtime.session_id = event.session_id OR profile.device_id
  REQUIRE adapters.input["sensor.mic"] exists
  micAdapter = adapters.input["sensor.mic"]
  audioInputChannel.ensureOpen(device_id = profile.device_id)
  audioOutputChannel.ensureOpen(device_id = profile.device_id)
  speakerSink = adapters.output["actuator.speaker"]
  speakerSink.prepare(realtime.default_speaker_format)
  realtime.mic_stream_id = newStreamId("stream_mic")
  realtime.mic_state = "open"

  control.send(Event(
    event_name = "control.audio_session.opened",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    session_id = realtime.session_id,
    payload = {
      reason = "device_upstream_ready",
      input_stream = {
        stream_id = realtime.mic_stream_id,
        stream_type = "sensor.mic",
        format = micAdapter.format
      }
    }
  ))

  START micUploadLoop()

FUNCTION micUploadLoop():
  LOOP while realtime.mic_state == "open":
    WAIT until micAdapter has next chunk
    chunk = micAdapter.readChunk()
    audioInputChannel.sendChunk(StreamChunk(
      user_id = profile.user_id,
      session_id = realtime.session_id,
      stream_id = realtime.mic_stream_id,
      stream_type = "sensor.mic",
      seq = nextSeq(realtime.mic_stream_id),
      payload = chunk.bytes,
      codec = chunk.codec,
      sample_rate = chunk.sample_rate,
      channels = chunk.channels,
      duration_ms = chunk.duration_ms,
      final = false
    ))

FUNCTION handleVisualOpenRequested(event):
  REQUIRE event.stream_type == "sensor.rgb"
  REQUIRE adapters.input["sensor.rgb"] exists
  visual_stream_id = event.stream_id OR newStreamId("stream_rgb")
  request = event.payload
  REQUIRE request.mode == "single"
  REQUIRE request.sample_count == null OR request.sample_count == 1
  visualAdapter = adapters.input["sensor.rgb"]

  TRY:
    visualInputChannel.ensureOpen(device_id = profile.device_id)
    visualSource = visualAdapter.open(request)
    realtime.visual_stream_id = visual_stream_id
    realtime.visual_state = "capturing_one_frame"

    control.send(Event(
      event_name = "stream.input.opened",
      user_id = profile.user_id,
      producer_id = profile.device_id,
      session_id = event.session_id OR realtime.session_id,
      stream_id = visual_stream_id,
      stream_type = "sensor.rgb",
      payload = {
        stream_type = "sensor.rgb",
        request_id = request.request_id,
        format = visualSource.format
      }
    ))

    frame = visualSource.readFrame()
    visualInputChannel.sendChunk(StreamChunk(
      user_id = profile.user_id,
      session_id = event.session_id OR realtime.session_id,
      stream_id = visual_stream_id,
      stream_type = "sensor.rgb",
      seq = 0,
      payload = frame.bytes,
      codec = frame.codec,
      sample_rate = 1,
      channels = 1,
      duration_ms = 0,
      final = true,
      metadata = {
        request_id = request.request_id,
        sample_index = 0,
        sample_count = 1,
        width = frame.width,
        height = frame.height,
        captured_at_ms = nowMs()
      }
    ))

    control.send(Event(
      event_name = "stream.input.closed",
      user_id = profile.user_id,
      producer_id = profile.device_id,
      session_id = event.session_id OR realtime.session_id,
      stream_id = visual_stream_id,
      stream_type = "sensor.rgb",
      payload = {
        stream_type = "sensor.rgb",
        request_id = request.request_id,
        reason = "single_frame_uploaded"
      }
    ))

  CATCH error:
    control.send(Event(
      event_name = "stream.input.failed",
      user_id = profile.user_id,
      producer_id = profile.device_id,
      session_id = event.session_id OR realtime.session_id,
      stream_id = visual_stream_id,
      stream_type = "sensor.rgb",
      payload = {
        stream_type = "sensor.rgb",
        request_id = request.request_id,
        reason = "visual_capture_failed",
        error = error.message
      }
    ))
  FINALLY:
    visualAdapter.close(visual_stream_id)
    realtime.visual_state = "closed"
    realtime.visual_stream_id = null

FUNCTION handleVisualCloseRequested(event):
  IF event.stream_type != "sensor.rgb":
    RETURN

  // 当前视觉链路是一请求一张。close 只用于取消尚未完成的采集，或兼容未来扩展模式。
  realtime.visual_state = "closing"
  visualAdapter = adapters.input["sensor.rgb"]
  visualAdapter.close(event.stream_id OR realtime.visual_stream_id)

  TRY:
    control.send(Event(
      event_name = "stream.input.closed",
      user_id = profile.user_id,
      producer_id = profile.device_id,
      session_id = event.session_id OR realtime.session_id,
      stream_id = event.stream_id OR realtime.visual_stream_id,
      stream_type = "sensor.rgb",
      payload = {
        stream_type = "sensor.rgb",
        request_id = event.payload.request_id,
        reason = "server_close_requested"
      }
    ))
  FINALLY:
    realtime.visual_state = "closed"
    realtime.visual_stream_id = null

FUNCTION handleSpeakerOutputOpenRequested(event):
  REQUIRE event.stream_type == "actuator.speaker"
  REQUIRE adapters.output["actuator.speaker"] exists
  speakerSink = adapters.output["actuator.speaker"]
  output = outputRegistry.open(event.stream_id, event.stream_type)
  playbackBuffer = playbackBuffers.resetOrCreate(
    stream_id = event.stream_id,
    config = sdkConfig.playback_buffer["actuator.speaker"]
  )
  IF event.payload.format differs from speakerSink.currentFormat:
    speakerSink.reconfigure(event.payload.format)
  output.playback_buffer_id = playbackBuffer.id
  output.pause_sent = false
  output.state = "opened"

  control.send(Event(
    event_name = "stream.output.ready",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    session_id = event.session_id OR realtime.session_id,
    stream_id = event.stream_id,
    stream_type = event.stream_type,
    payload = {
      stream_type = event.stream_type,
      reason = "device_speaker_ready",
      format = event.payload.format
    }
  ))

  // 这里准备的是本轮逻辑 speaker output stream，不重新打开 audio output WebSocket，
  // 也不重建 session 级 speaker sink；除非本轮音频格式变化，才重新配置 sink。
  // server 必须等到 stream.output.ready 后，才向已建立的音频下行链路写入 speaker chunk。

FUNCTION onSpeakerOutputChunk(chunk):
  output = outputRegistry.get(chunk.stream_id)
  playbackBuffer = playbackBuffers.get(output.playback_buffer_id)
  speakerSink = adapters.output["actuator.speaker"]
  playbackBuffer.enqueue(chunk)

  IF output.state != "started" AND playbackBuffer.bufferedMs() >= playbackBuffer.startWatermarkMs:
    START playbackDrainLoop(chunk.stream_id)
    output.state = "started"
    control.send(Event(
      event_name = "stream.output.started",
      user_id = profile.user_id,
      producer_id = profile.device_id,
      session_id = chunk.session_id,
      stream_id = chunk.stream_id,
      stream_type = chunk.stream_type,
      payload = { stream_type = chunk.stream_type }
    ))

  IF playbackBuffer.bufferedMs() >= playbackBuffer.highWatermarkMs AND output.pause_sent == false:
    output.pause_sent = true
    control.send(Event(
      event_name = "downstream.pause.requested",
      user_id = chunk.user_id,
      producer_id = profile.device_id,
      session_id = chunk.session_id,
      stream_id = chunk.stream_id,
      stream_type = chunk.stream_type,
      payload = {
        stream_type = chunk.stream_type,
        buffered_ms = playbackBuffer.bufferedMs(),
        high_watermark_ms = playbackBuffer.highWatermarkMs,
        reason = "speaker_buffer_high"
      }
    ))

FUNCTION playbackDrainLoop(stream_id):
  output = outputRegistry.get(stream_id)
  playbackBuffer = playbackBuffers.get(output.playback_buffer_id)
  speakerSink = adapters.output["actuator.speaker"]

  LOOP while output.state != "cancelled":
    audioChunk = playbackBuffer.readNextOrWait()
    IF audioChunk exists:
      speakerSink.writeChunk(audioChunk)
    onPlaybackBufferDrained(stream_id)
    IF playbackBuffer.isFinalDrained():
      BREAK

FUNCTION onPlaybackBufferDrained(stream_id):
  output = outputRegistry.get(stream_id)
  playbackBuffer = playbackBuffers.get(output.playback_buffer_id)
  IF output.pause_sent == true AND playbackBuffer.bufferedMs() <= playbackBuffer.lowWatermarkMs:
    output.pause_sent = false
    control.send(Event(
      event_name = "downstream.resume.requested",
      user_id = output.user_id,
      producer_id = profile.device_id,
      session_id = output.session_id,
      stream_id = stream_id,
      stream_type = output.stream_type,
      payload = {
        stream_type = output.stream_type,
        buffered_ms = playbackBuffer.bufferedMs(),
        low_watermark_ms = playbackBuffer.lowWatermarkMs,
        reason = "speaker_buffer_low"
      }
    ))

FUNCTION handleSpeakerOutputCloseRequested(event):
  output = outputRegistry.get(event.stream_id)
  playbackBuffer = playbackBuffers.get(output.playback_buffer_id)
  speakerSink = adapters.output["actuator.speaker"]
  output.state = "closing"
  playbackBuffer.markFinal()
  playbackBuffer.waitFinalDrained()
  speakerSink.drain(event.stream_id)
  speakerSink.close(event.stream_id)
  playbackBuffers.remove(event.stream_id)
  outputRegistry.remove(event.stream_id)

  control.send(Event(
    event_name = "stream.output.closed",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    session_id = event.session_id,
    stream_id = event.stream_id,
    stream_type = event.stream_type,
    payload = {
      stream_type = event.stream_type,
      reason = "speaker_output_closed"
    }
  ))

FUNCTION handleSpeakerOutputCancelRequested(event):
  output = outputRegistry.get(event.stream_id)
  playbackBuffer = playbackBuffers.get(output.playback_buffer_id)
  speakerSink = adapters.output["actuator.speaker"]
  output.state = "cancelled"
  playbackBuffer.clear()
  speakerSink.cancel(event.stream_id)
  playbackBuffers.remove(event.stream_id)
  outputRegistry.remove(event.stream_id)

  control.send(Event(
    event_name = "stream.output.closed",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    session_id = event.session_id,
    stream_id = event.stream_id,
    stream_type = event.stream_type,
    payload = {
      stream_type = event.stream_type,
      reason = "server_cancel_requested"
    }
  ))

FUNCTION handleAudioSessionCloseRequested(event):
  realtime.mic_state = "closing"
  micAdapter = adapters.input["sensor.mic"]
  micAdapter.close()

  outputRegistry.waitAllPlaybackDrain()
  control.send(Event(
    event_name = "control.audio_session.closed",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    session_id = realtime.session_id,
    payload = { reason = "device_audio_session_closed" }
  ))

  realtime.reset()
```

## 4. 设备消费其他 Server 事件

本节只覆盖注册、实时对话主链路之外的事件。标准协议事件不应该在本节继续扩展含义。`command.requested` 属于标准命令事件族；`stream.output.open.requested` / `stream.output.close.requested` / `stream.output.cancel.requested` 属于标准 output stream 生命周期，已经在第 3 节作为 `actuator.speaker` 下行播放生命周期讲过。为了避免和这些内置事件族冲突，业务扩展必须使用 `custom.*` 事件名。图中 `SDK -> App` 的箭头表示 SDK 调用宿主应用的本地回调，不是控制面协议事件。

SDK 事件路由必须用事件命名空间隔离标准事件和自定义事件，而不是只靠“未命中内置 handler 再兜底”的隐式约定。标准事件继续使用 `control.*`、`stream.*`、`command.*`、`system.*` 等命名空间；所有业务扩展或 App 自定义事件必须使用 `custom.*` 命名空间。

路由规则必须固定为：

1. `event_name` 以 `custom.` 开头：SDK 不进入任何标准内置状态机，只进入自定义事件分发器。
2. `event_name` 不以 `custom.` 开头：SDK 按标准协议处理，只能进入注册、音频会话、视觉单帧采集、speaker 播放、标准命令等内置状态机。
3. 标准事件不能再投递给自定义 `on_event`。因此 `stream.output.open.requested (actuator.speaker)` 只会进入 speaker 播放状态机，不会同时触发自定义事件回调。

未来 SDK 应支持的其他消费入口：

| server 事件 | SDK 入口 | 代表含义 | App 应做什么 |
| --- | --- | --- | --- |
| `custom.command.requested` | `on_custom_command(...)` / `onCustomCommand(...)`；也可以用 `on_event(...)` | 业务自定义的低频端侧动作，例如业务模式切换、peer video 控制、自定义硬件动作 | 执行业务命令；如需回报业务结果，使用 `ctx.emit("custom.<domain>.<event>", payload)` 发送自定义事件 |
| 其他 `custom.<domain>.*` | `on_event(...)` / `onEvent(...)` / `onEvent` | 项目扩展协议事件或 App 自定义事件 | App 自行解释 payload，并按该事件协议回执 |

自定义事件命名建议使用 `custom.<app_or_domain>.<event_name>`，必要时追加状态后缀，例如 `custom.navigation.route.updated`。`custom.command.*` 是独立的自定义事件族，只用于业务扩展；不能替代标准 `command.*`。speaker 音频播放永远使用标准 `stream.output.* (actuator.speaker)` 链路。非音频业务动作优先使用 `custom.command.requested` 或普通 `custom.<domain>.*` 事件，不暴露 `custom.output.*` 作为 App 开发者主路径。

当前协议实现已经允许 `custom.*` 事件名通过 schema 和 server 运行时校验。App 通过端侧 SDK 的 `on_custom_command(...)` / `on_event(...)` 注册回调后，SDK 会在设备注册 payload 的 properties 中声明自定义消费能力，server 再据此生成 `custom.command.requested` 或具体 `custom.<domain>.*` 投递路由。App 不需要手写 routes。

本节伪代码里要区分两类函数：

- `on_event(...)`、`on_custom_command(...)` 是 SDK 暴露给 App 的公开注册 API，App 用它们注册回调。
- `handle*` / `dispatch*` 是 SDK 内部路由函数，只负责把 server 事件转成对应回调调用，不是 App 直接使用的 API。

### 4.1 总时序图

```plantuml
@startuml
participant Server
participant "Device SDK" as SDK
participant "Device App" as App

Server -> SDK: control event
SDK -> SDK: dispatch by event_name and stream_type
alt custom.command.* event
  SDK -> App: onCustomCommand(event)
else other custom.* event
  SDK -> App: onEvent(event)
else built-in realtime or speaker event
  SDK -> SDK: handle by built-in state machine
  SDK -> SDK: stop dispatch
end
@enduml
```

### 4.2 App 回调注册伪代码

```text
FUNCTION on_event(event_name, callback):
  REQUIRE event_name starts with "custom."
  callbackRegistry.custom_events[event_name].append(callback)

FUNCTION on_custom_command(command_name, callback):
  callbackRegistry.custom_commands[command_name] = callback

// App side usage:
sdk.on_custom_command("haptic.vibrate", handleVibrate)
sdk.on_event("custom.navigation.route.updated", handleRouteUpdated)
```

### 4.3 SDK 内部事件路由伪代码

```text
FUNCTION dispatchServerEvent(event):
  IF event.event_name starts with "custom.":
    dispatchCustomEvent(event)
    RETURN

  SWITCH event.event_name:
    CASE "control.audio_session.open.requested":
      handleAudioSessionOpenRequested(event)
      RETURN

    CASE "control.audio_session.close.requested":
      handleAudioSessionCloseRequested(event)
      RETURN

    CASE "stream.control.open.requested":
      // Visual single-frame capture is defined in section 3.
      handleInputOpenRequested(event)
      RETURN

    CASE "stream.control.close.requested":
      // Visual single-frame capture is defined in section 3.
      IF event.stream_type == "sensor.rgb":
        handleVisualCloseRequested(event)
      ELSE:
        handleInputCloseRequested(event)
      RETURN

    CASE "stream.output.open.requested":
      handleStandardOutputOpenRequested(event)
      RETURN

    CASE "stream.output.close.requested":
      handleStandardOutputCloseRequested(event)
      RETURN

    CASE "stream.output.cancel.requested":
      handleStandardOutputCancelRequested(event)
      RETURN

    CASE "command.requested":
      handleStandardCommandRequested(event)
      RETURN

    DEFAULT:
      callbacks.onUnhandledServerEvent(event)

FUNCTION dispatchCustomEvent(event):
  IF event.event_name == "custom.command.requested":
    handleCustomCommandRequested(event)
    RETURN

  eventCallbacks = callbackRegistry.custom_events[event.event_name]
  IF eventCallbacks is empty:
    callbacks.onUnhandledServerEvent(event)
    RETURN

  FOR callback in eventCallbacks:
    callback(event)
```

### 4.4 输入采集 consumer 伪代码

本小节是第 3 节视觉单帧采集链路的 SDK 内部路由细节，放在这里是为了展示 consumer 分发形态；它不是本节定义的“其他事件”。

```text
FUNCTION handleInputOpenRequested(event):
  IF event.stream_type == "sensor.rgb":
    handleVisualOpenRequested(event)
    RETURN

  IF event.stream_type is supported by application:
    openGenericSensorStream(event)
    RETURN

  control.send(Event(
    event_name = "stream.input.failed",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    session_id = event.session_id,
    stream_id = event.stream_id,
    stream_type = event.stream_type,
    payload = {
      stream_type = event.stream_type,
      reason = "unsupported_stream_type"
    }
  ))

FUNCTION handleInputCloseRequested(event):
  callbacks.closeSensor(event.stream_type, event.stream_id)
  control.send(Event(
    event_name = "stream.input.closed",
    user_id = profile.user_id,
    producer_id = profile.device_id,
    session_id = event.session_id,
    stream_id = event.stream_id,
    stream_type = event.stream_type,
    payload = {
      stream_type = event.stream_type,
      reason = "server_close_requested"
    }
  ))
```

### 4.5 自定义命令 consumer 伪代码

```text
FUNCTION handleCustomCommandRequested(event):
  command = event.payload.command
  commandCallback = callbackRegistry.custom_commands[command]

  IF commandCallback does not exist:
    callbacks.onUnhandledServerEvent(event)
    RETURN

  TRY:
    commandContext = CustomCommandContext(event)
    commandCallback(commandContext)

  CATCH error:
    callbacks.onHandlerError(event, error)

TYPE CustomCommandContext:
  event
  payload = event.payload

  FUNCTION emit(event_name, payload):
    REQUIRE event_name starts with "custom."
    control.send(Event(
      event_name = event_name,
      user_id = event.user_id,
      producer_id = profile.device_id,
      session_id = event.session_id,
      payload = payload
    ))
```

## 5. 跨语言 SDK 必须保持一致的状态机

```plantuml
@startuml
[*] --> Disconnected
Disconnected --> ControlConnected : control websocket open
ControlConnected --> Registering : send register.requested
Registering --> Registered : registered
Registering --> RegisterFailed : register.failed
Registered --> DialogOpening : audio_session.open.requested
DialogOpening --> DialogOpen : audio_session.opened
DialogOpen --> DialogClosing : audio_session.close.requested
DialogClosing --> Registered : audio_session.closed
Registered --> Disconnected : control websocket closed
DialogOpen --> Disconnected : control websocket closed
RegisterFailed --> Disconnected
@enduml
```

状态机伪代码：

```text
STATE Disconnected:
  on connect requested:
    open control websocket
    move to ControlConnected

STATE ControlConnected:
  on control opened:
    send registration
    move to Registering

STATE Registering:
  on registered:
    start heartbeat
    move to Registered
  on register failed:
    stop heartbeat
    move to RegisterFailed

STATE Registered:
  on audio_session.open.requested:
    ensure audio input and audio output channels
    move to DialogOpening
  on control closed:
    stop heartbeat
    move to Disconnected

STATE DialogOpening:
  on audio input and audio output channels ready:
    send audio_session.opened
    move to DialogOpen

STATE DialogOpen:
  on mic chunk:
    send StreamChunk sensor.mic
  on visual request:
    capture and upload one sensor.rgb frame
  on output request:
    reset per-output state and send stream.output.ready
  on speaker chunk:
    enqueue output and send stream.output.started when playback starts
  on custom.command.requested:
    dispatch custom command callback
  on audio_session.close.requested:
    close audio session and drain playback
    move to DialogClosing

STATE DialogClosing:
  on session resources closed:
    send audio_session.closed
    move to Registered
```

## 6. 契约测试建议

每个语言 SDK 至少应有以下契约测试。测试可以使用本地 loopback server，不需要真实模型和真实硬件。

1. 注册成功：SDK 发送 `control.device.register.requested`，收到 `registered` 后启动心跳。
2. 注册失败：SDK 收到 `register.failed` 后不启动心跳，并向应用暴露失败原因。
3. 实时音频打开：收到 `control.audio_session.open.requested` 后，SDK 建立或复用音频上行和音频下行两条物理链路，确认 `sensor.mic` 可读后回 `control.audio_session.opened`，并在 payload 中声明 `sensor.mic` 上行 stream。
4. 麦克风连续上传：SDK 在 `control.audio_session.opened` 后持续发送 `sensor.mic` chunk，且 `final=True` 不代表端侧 VAD 语音结束。
5. 视觉单帧采集：收到 `stream.control.open.requested (sensor.rgb, mode=single, sample_count=1)` 后，SDK 建立或复用视觉上行链路，回 `stream.input.opened`，上传一个 `final=true` 的 RGB chunk，然后回 `stream.input.closed`。
6. 输出播放：收到标准 `stream.output.open.requested (actuator.speaker)` 后，SDK 在已建立的音频下行链路和 session 级 speaker runtime 上重置本轮逻辑 output stream 状态，并先回 `stream.output.ready`；server 收到该回执后才能下发音频 chunk。SDK 达到起播水位并开始写 speaker sink 时回 `stream.output.started`；收到 `stream.output.close.requested` 后，等待本地 drain，再回 `stream.output.closed`。
7. 自定义命令执行：收到 `custom.command.requested` 后，SDK 调用 App 通过 `on_custom_command(...)` 注册的回调；如需回报业务结果，handler 使用 `ctx.emit("custom.<domain>.<event>", payload)`。
8. 关闭会话：收到 `control.audio_session.close.requested` 后，SDK 结束本次音频会话、等待播放 drain、回 `control.audio_session.closed`。
