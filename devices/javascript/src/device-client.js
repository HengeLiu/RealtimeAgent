import {CustomCommandContext} from "./custom-command-context.js";
import {buildRegistrationPayload} from "./device-profile.js";
import {DeviceDiagnostics} from "./diagnostics.js";
import {
  createEvent,
  eventName,
  eventPayload,
  eventSessionId,
  eventStreamId,
  eventStreamType,
  parseEvent,
  PROTOCOL_VERSION,
} from "./event.js";
import {AudioInput, Camera, Speaker} from "./options.js";
import {decodeStreamChunk, encodeStreamChunk, StreamChunk} from "./stream-chunk.js";
import {BrowserCameraFrameSource} from "./media/browser-camera-frame-source.js";
import {BrowserMicrophoneSource} from "./media/browser-microphone-source.js";
import {BrowserSpeakerSink} from "./media/browser-speaker-sink.js";
import {NoopSpeakerSink} from "./media/noop-speaker-sink.js";
import {SpeakerPlaybackBuffer} from "./media/speaker-playback-buffer.js";
import {BrowserWebSocketTransport, StreamChannel} from "./transport/browser-websocket-transport.js";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class DeviceClient {
  /**
   * 创建 JavaScript Device SDK client。
   *
   * 主要逻辑：组合设备 profile、控制通道、媒体 adapter 和事件状态机。
   * 参数：serverUrl、deviceId、userId、硬件能力和可选 transport。
   * 返回值：DeviceClient 实例。
   * 异常情况：缺少必要设备身份或 URL 无效时抛出错误。
   */
  constructor({
    serverUrl,
    deviceId,
    userId,
    name,
    clientType = "javascript",
    sdkVersion = "realtime-agent-javascript-device-sdk-0.1.0",
    runtime = undefined,
    audioInput = AudioInput.disabled(),
    camera = Camera.disabled(),
    speaker = Speaker.disabled(),
    auth = null,
    properties = {},
    protocolVersion = PROTOCOL_VERSION,
    transport = null,
    logLevel = "info",
  }) {
    if (!serverUrl || !deviceId || !userId) {
      throw new Error("serverUrl, deviceId and userId are required");
    }
    this.serverUrl = serverUrl;
    this.deviceId = deviceId;
    this.userId = userId;
    this.name = name ?? deviceId;
    this.clientType = clientType;
    this.sdkVersion = sdkVersion;
    this.runtime = runtime;
    this.auth = auth;
    this.properties = properties;
    this.protocolVersion = protocolVersion;
    this.transport = transport ?? new BrowserWebSocketTransport();
    this.logLevel = logLevel;

    this.audioInput = this.installAudioInput(audioInput);
    this.camera = this.installCamera(camera);
    this.speaker = this.installSpeaker(speaker);

    this.diagnostics = new DeviceDiagnostics();
    this.customCommandHandlers = new Map();
    this.eventHandlers = new Map();
    this.connectionStateHandlers = new Set();
    this.conversationStateHandlers = new Set();
    this.debugLogHandlers = new Set();
    this.connectedStreamChannels = new Set();
    this.sequenceByStream = new Map();
    this.outputStates = new Map();
    this.completedOutputStreams = new Set();
    this.heartbeatTimer = null;
    this.controlReceiveLoopRunning = false;
    this.streamReceiveLoopRunning = false;
    this.microphoneStop = null;
    this.closed = false;
  }

  onCustomCommand(command, handler) {
    this.customCommandHandlers.set(command, handler);
  }

  onEvent(eventName, handler) {
    if (!eventName.startsWith("custom.")) {
      throw new Error("onEvent only accepts custom.* events");
    }
    this.eventHandlers.set(eventName, handler);
  }

  onConnectionStateChange(handler) {
    this.connectionStateHandlers.add(handler);
  }

  onConversationStateChange(handler) {
    this.conversationStateHandlers.add(handler);
  }

  onDebugLog(handler) {
    this.debugLogHandlers.add(handler);
  }

  diagnosticsSnapshot() {
    return this.diagnostics.snapshot();
  }

  async requestPermissions() {
    const microphone = this.audioInput.enabled && this.audioInput.source?.requestPermission
      ? await this.audioInput.source.requestPermission()
      : {state: this.audioInput.enabled ? "unavailable" : "notRequired"};
    const camera = this.camera.enabled && this.camera.source?.requestPermission
      ? await this.camera.source.requestPermission()
      : {state: this.camera.enabled ? "unavailable" : "notRequired"};
    if (this.speaker.enabled && this.speaker.sink?.prepareAudioContext) {
      await this.speaker.sink.prepareAudioContext();
    }
    const status = {
      microphone: microphone.state ?? "granted",
      camera: camera.state ?? "granted",
      isAuthorized: [microphone.state, camera.state].every((state) => {
        return !state || state === "notRequired" || state === "granted";
      }),
      details: {microphone, camera},
    };
    this.diagnostics.mic.permission = status.microphone;
    this.diagnostics.camera.permission = status.camera;
    return status;
  }

  async connect() {
    await this.emitConnectionState("connecting");
    try {
      await this.transport.connectControl(this.websocketUrl("/ws/control"));
      this.diagnostics.controlState = "connected";
    } catch (error) {
      this.diagnostics.lastError = error.message;
      this.diagnostics.controlState = "connect_failed";
      await this.emitConnectionState("idle");
      throw error;
    }
  }

  async register({startHeartbeat = true} = {}) {
    if (this.diagnostics.controlState !== "connected") {
      await this.connect();
    }
    await this.emitConnectionState("registering");
    await this.sendEvent("control.device.register.requested", this.registrationPayload());
    while (true) {
      const event = await this.receiveEvent();
      if (eventName(event) === "control.device.registered") {
        this.diagnostics.registered = true;
        this.diagnostics.controlState = "registered";
        await this.emitConnectionState("registered");
        if (startHeartbeat) {
          const interval = Number(eventPayload(event).heartbeat_interval_seconds ?? 10);
          this.startHeartbeat(interval);
        }
        return event;
      }
      if (eventName(event) === "control.device.register.failed") {
        const reason = String(eventPayload(event).reason ?? "unknown");
        this.diagnostics.lastError = reason;
        await this.emitConnectionState("idle");
        throw new Error(`设备注册失败：${reason}`);
      }
    }
  }

  async startConversation({reason = "app_start_requested"} = {}) {
    if (!this.diagnostics.registered) {
      await this.register();
    }
    this.startControlReceiveLoop();
    await this.ensureConfiguredStreams();
    if (this.speaker.enabled) this.startAudioOutputReceiveLoop();
    await this.emitConversationState("starting");
    await this.sendEvent("control.user.wake.detected", {
      reason,
      audio_input: this.audioInput.enabled,
      speaker: this.speaker.enabled,
      camera: this.camera.enabled,
    });
  }

  async requestConversationClose({reason = "app_requested"} = {}) {
    if (this.diagnostics.conversationState === "waiting") {
      await this.emitConversationState("waiting");
      return;
    }
    if (this.diagnostics.conversationState !== "closing") {
      await this.emitConversationState("closing");
    }
    await this.sendEvent("control.user.dialog.close.requested", {reason});
  }

  async close({force = false} = {}) {
    this.closed = true;
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
    await this.stopMicrophone();
    for (const state of this.outputStates.values()) {
      state.finishAbort = true;
      await state.buffer?.cancel();
    }
    this.outputStates.clear();
    this.connectedStreamChannels.clear();
    await this.transport.close();
    this.diagnostics.registered = false;
    this.diagnostics.controlState = force ? "closed" : "closed";
    this.diagnostics.streamState = "closed";
    await this.emitConnectionState("closed");
    await this.emitConversationState("waiting");
  }

  registrationPayload() {
    return buildRegistrationPayload({
      deviceId: this.deviceId,
      userId: this.userId,
      name: this.name,
      clientType: this.clientType,
      sdkVersion: this.sdkVersion,
      runtime: this.runtime,
      properties: this.properties,
      auth: this.auth,
      audioInput: this.audioInput,
      camera: this.camera,
      speaker: this.speaker,
      customCommands: [...this.customCommandHandlers.keys()],
      customEventSubscriptions: [...this.eventHandlers.keys()],
    });
  }

  async sendEvent(eventNameValue, payload = {}, {sessionId, streamId, streamType} = {}) {
    const event = createEvent({
      eventName: eventNameValue,
      userId: this.userId,
      producerId: this.deviceId,
      payload,
      sessionId,
      streamId,
      streamType,
      version: this.protocolVersion,
    });
    await this.transport.sendControl(JSON.stringify(event));
    this.diagnostics.sentEvents += 1;
    this.diagnostics.lastEventName = eventNameValue;
    await this.debugLog(`control -> ${eventNameValue} stream=${streamId ?? "-"} type=${streamType ?? "-"}`);
    return event;
  }

  async receiveEvent() {
    const text = await this.transport.receiveControl();
    const event = parseEvent(text);
    this.diagnostics.receivedEvents += 1;
    this.diagnostics.lastEventName = eventName(event);
    await this.debugLog(`control <- ${eventName(event)} stream=${eventStreamId(event) ?? "-"} type=${eventStreamType(event) ?? "-"}`);
    return event;
  }

  async dispatchEvent(event) {
    const name = eventName(event);
    if (name.startsWith("custom.")) return this.dispatchCustomEvent(event);
    switch (name) {
      case "control.audio_session.open.requested":
        await this.handleAudioSessionOpen(event);
        return true;
      case "control.audio_session.close.requested":
        await this.handleAudioSessionClose(event);
        return true;
      case "custom.command.requested":
        return this.dispatchCustomEvent(event);
      case "command.requested":
        await this.sendEvent("command.failed", {
          command_id: eventPayload(event).command_id,
          error: {code: "unhandled_command", message: "standard command is not handled by JavaScript demo SDK"},
        }, {sessionId: eventSessionId(event)});
        return true;
      case "stream.control.open.requested":
        await this.handleStreamOpen(event);
        return true;
      case "stream.control.close.requested":
        await this.handleStreamClose(event);
        return true;
      case "stream.output.start.requested":
        await this.handleOutputStart(event);
        return true;
      case "stream.output.finish.requested":
        await this.handleOutputFinish(event);
        return true;
      case "stream.output.cancel.requested":
        await this.handleOutputCancel(event);
        return true;
      default:
        return false;
    }
  }

  async sendStreamChunk(chunk) {
    const channel = this.streamChannelFor(chunk.streamType);
    await this.ensureStream(channel);
    await this.transport.sendStream(encodeStreamChunk(chunk), channel);
    this.diagnostics.sentStreamChunks += 1;
  }

  async dispatchStreamChunk(chunk) {
    if (chunk.streamType !== "actuator.speaker" || !this.speaker.enabled) return false;
    if (this.completedOutputStreams.has(chunk.streamId)) {
      await this.debugLog(`speaker late chunk ignored stream=${chunk.streamId} seq=${chunk.seq}`);
      return false;
    }
    const state = await this.ensureOutputState({
      sessionId: chunk.sessionId,
      streamId: chunk.streamId,
      streamType: chunk.streamType,
      codec: chunk.codec,
      sampleRate: chunk.sampleRate,
      channels: chunk.channels,
    });
    const actions = await state.buffer.append(chunk);
    state.appendedLastSeq = Math.max(state.appendedLastSeq ?? -1, chunk.seq);
    this.diagnostics.receivedOutputChunks += 1;
    this.diagnostics.speaker = state.buffer.snapshot();
    await this.handlePlaybackActions(chunk.streamId, actions);
    this.startDrainLoop(chunk.streamId);
    return true;
  }

  async handleAudioSessionOpen(event) {
    const sessionId = eventSessionId(event) ?? this.deviceId;
    await this.ensureConfiguredStreams();
    if (this.speaker.enabled) this.startAudioOutputReceiveLoop();
    await this.sendEvent("control.audio_session.opened", {
      audio_input: this.audioInput.enabled,
      speaker: this.speaker.enabled,
      camera: this.camera.enabled,
      input_stream: this.audioInput.enabled ? {
        stream_type: "sensor.mic",
        format: {
          codec: this.audioInput.configuration.codec,
          sample_rate: this.audioInput.configuration.sampleRate,
          channels: this.audioInput.configuration.channels,
          chunk_ms: this.audioInput.configuration.chunkMs,
        },
      } : null,
    }, {sessionId});
    await this.emitConversationState("active");
    if (this.audioInput.enabled) {
      await this.startMicrophone(sessionId);
    }
  }

  async handleAudioSessionClose(event) {
    const sessionId = eventSessionId(event) ?? this.deviceId;
    await this.emitConversationState("closing");
    await this.stopMicrophone();
    for (const state of this.outputStates.values()) {
      state.finishAbort = true;
      await state.buffer?.cancel();
    }
    this.outputStates.clear();
    await this.sendEvent("control.audio_session.closed", {
      reason: eventPayload(event).reason ?? "server_requested",
    }, {sessionId});
    await this.emitConversationState("waiting");
  }

  async handleStreamOpen(event) {
    const streamType = eventStreamType(event);
    if (streamType !== "sensor.rgb" || !this.camera.enabled) {
      await this.sendEvent("stream.input.failed", {
        stream_type: streamType,
        error: {code: "unsupported_stream", message: `${streamType} is not enabled`},
      }, {
        sessionId: eventSessionId(event),
        streamId: eventStreamId(event),
        streamType,
      });
      return;
    }
    const sessionId = eventSessionId(event) ?? this.deviceId;
    const streamId = eventStreamId(event) ?? `stream_rgb_${Date.now()}`;
    try {
      await this.ensureStream(StreamChannel.visualInput);
      const frame = await this.camera.source.readFrame(eventPayload(event));
      const frameFormat = {
        codec: frame.codec ?? this.camera.format ?? "jpeg",
        sample_rate: frame.sampleRate ?? 1,
        channels: frame.channels ?? 1,
        chunk_ms: frame.durationMs ?? 0,
      };
      await this.sendEvent("stream.input.opened", {
        stream_type: "sensor.rgb",
        request_id: eventPayload(event).request_id,
        format: frameFormat,
      }, {sessionId, streamId, streamType});
      await this.sendStreamChunk(new StreamChunk({
        userId: this.userId,
        sessionId,
        streamId,
        streamType,
        seq: 0,
        payload: frame.payload,
        codec: frameFormat.codec,
        sampleRate: frameFormat.sample_rate,
        channels: frameFormat.channels,
        durationMs: frameFormat.chunk_ms,
        final: true,
        metadata: {request_id: eventPayload(event).request_id, ...(frame.metadata ?? {})},
      }));
      await this.debugLog(`visual frame sent stream=${streamId} codec=${frameFormat.codec} bytes=${frame.payload.byteLength ?? frame.payload.length ?? 0}`);
      await this.sendEvent("stream.input.closed", {
        stream_type: "sensor.rgb",
        reason: "single_frame_sent",
      }, {sessionId, streamId, streamType});
    } catch (error) {
      await this.sendEvent("stream.input.failed", {
        stream_type: "sensor.rgb",
        error: {code: "camera.capture_failed", message: error.message},
      }, {sessionId, streamId, streamType});
    }
  }

  async handleStreamClose(event) {
    if (eventStreamType(event) === "sensor.mic") {
      await this.stopMicrophone();
    }
  }

  async handleOutputStart(event) {
    if (eventStreamType(event) !== "actuator.speaker" || !this.speaker.enabled) return;
    const state = await this.ensureOutputState({
      sessionId: eventSessionId(event) ?? this.deviceId,
      streamId: eventStreamId(event),
      streamType: "actuator.speaker",
      codec: eventPayload(event).codec ?? "pcm16le",
      sampleRate: Number(eventPayload(event).sample_rate ?? 24000),
      channels: Number(eventPayload(event).channels ?? 1),
    });
    await this.markOutputReady(state);
  }

  async handleOutputFinish(event) {
    const streamId = eventStreamId(event);
    const state = this.outputStates.get(streamId);
    if (!state) {
      await this.sendEvent("stream.output.finished", {
        stream_type: "actuator.speaker",
        reason: "empty_output",
      }, {sessionId: eventSessionId(event), streamId, streamType: "actuator.speaker"});
      return;
    }
    const expectedLastSeq = eventPayload(event).output_last_seq;
    if (state.finishTask) return;
    state.finishAbort = false;
    state.finishTask = this.finishOutput(state, expectedLastSeq).finally(() => {
      if (this.outputStates.get(streamId) === state) {
        state.finishTask = null;
      }
    });
    void state.finishTask;
  }

  async handleOutputCancel(event) {
    const streamId = eventStreamId(event);
    const state = this.outputStates.get(streamId);
    if (state) {
      state.finishAbort = true;
      await state.buffer.cancel();
      this.outputStates.delete(streamId);
    }
    this.completedOutputStreams.add(streamId);
    await this.sendEvent("stream.output.cancelled", {
      stream_type: "actuator.speaker",
      reason: "cancel_requested",
    }, {sessionId: eventSessionId(event), streamId, streamType: "actuator.speaker"});
  }

  async finishOutput(state, expectedLastSeq) {
    const startedAt = Date.now();
    while (!state.finishAbort && expectedLastSeq !== undefined && !state.buffer.hasSeq(Number(expectedLastSeq))) {
      if (Date.now() - startedAt > 2000) break;
      await sleep(20);
    }
    if (state.finishAbort) return;
    try {
      await state.buffer.drainSink();
      if (state.finishAbort) return;
      this.completedOutputStreams.add(state.streamId);
      this.outputStates.delete(state.streamId);
      await this.sendEvent("stream.output.finished", {
        stream_type: "actuator.speaker",
      }, {sessionId: state.sessionId, streamId: state.streamId, streamType: state.streamType});
    } catch (error) {
      await this.sendEvent("stream.output.failed", {
        stream_type: "actuator.speaker",
        error: {code: "speaker.finish_failed", message: error.message},
      }, {sessionId: state.sessionId, streamId: state.streamId, streamType: state.streamType});
    }
  }

  async startMicrophone(sessionId) {
    await this.stopMicrophone();
    await this.ensureStream(StreamChannel.audioInput);
    const streamId = `stream_mic_${Date.now().toString(16)}`;
    let seq = 0;
    const sendPayload = async (payload) => {
      try {
        await this.sendStreamChunk(new StreamChunk({
          userId: this.userId,
          sessionId,
          streamId,
          streamType: "sensor.mic",
          seq: seq++,
          payload,
          codec: this.audioInput.configuration.codec,
          sampleRate: this.audioInput.configuration.sampleRate,
          channels: this.audioInput.configuration.channels,
          durationMs: this.audioInput.configuration.chunkMs,
          final: false,
        }));
        this.diagnostics.mic.seq = seq;
        this.diagnostics.mic.bytesSent = (this.diagnostics.mic.bytesSent ?? 0) + payload.byteLength;
      } catch (error) {
        await this.handleConnectionLost({type: "streamReceiveFailed", message: error.message});
      }
    };
    await this.audioInput.source.start({
      configuration: this.audioInput.configuration,
      diagnostics: this.diagnostics,
      onChunk: (payload) => { void sendPayload(payload); },
    });
    this.microphoneStop = async () => this.audioInput.source.stop?.();
  }

  async stopMicrophone() {
    if (this.microphoneStop) {
      await this.microphoneStop();
      this.microphoneStop = null;
    }
  }

  async dispatchCustomEvent(event) {
    if (eventName(event) === "custom.command.requested") {
      const command = eventPayload(event).command;
      const handler = this.customCommandHandlers.get(command);
      if (!handler) return false;
      await handler(new CustomCommandContext({client: this, event}));
      return true;
    }
    const handler = this.eventHandlers.get(eventName(event));
    if (!handler) return false;
    await handler(event);
    return true;
  }

  async ensureConfiguredStreams() {
    if (this.audioInput.enabled) await this.ensureStream(StreamChannel.audioInput);
    if (this.speaker.enabled) await this.ensureStream(StreamChannel.audioOutput);
  }

  async ensureStream(channel) {
    if (this.connectedStreamChannels.has(channel)) return;
    await this.transport.connectStream(channel, this.websocketUrl(this.streamPath(channel), {device_id: this.deviceId}));
    this.connectedStreamChannels.add(channel);
    this.diagnostics.streamState = "connected";
    await this.debugLog(`stream connected channel=${channel}`);
  }

  startControlReceiveLoop() {
    if (this.controlReceiveLoopRunning) return;
    this.controlReceiveLoopRunning = true;
    (async () => {
      while (!this.closed && this.controlReceiveLoopRunning) {
        try {
          const event = await this.receiveEvent();
          await this.dispatchEvent(event);
        } catch (error) {
          if (!this.closed) await this.handleConnectionLost({type: "controlReceiveFailed", message: error.message});
          return;
        }
      }
    })();
  }

  startAudioOutputReceiveLoop() {
    if (this.streamReceiveLoopRunning) return;
    this.streamReceiveLoopRunning = true;
    (async () => {
      while (!this.closed && this.streamReceiveLoopRunning) {
        try {
          await this.ensureStream(StreamChannel.audioOutput);
          const data = await this.transport.receiveStream(StreamChannel.audioOutput);
          this.diagnostics.receivedStreamChunks += 1;
          await this.dispatchStreamChunk(decodeStreamChunk(data));
        } catch (error) {
          if (!this.closed) {
            this.connectedStreamChannels.delete(StreamChannel.audioOutput);
            await sleep(100);
          }
        }
      }
    })();
  }

  startHeartbeat(intervalSeconds) {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => {
      this.sendEvent("control.device.heartbeat.received", {
        connection_state: "online",
        client_type: this.clientType,
      }).catch((error) => {
        void this.handleConnectionLost({type: "heartbeatFailed", message: error.message});
      });
    }, Math.max(1, intervalSeconds) * 1000);
    this.heartbeatTimer.unref?.();
  }

  async ensureOutputState({sessionId, streamId, streamType, codec, sampleRate, channels}) {
    const id = streamId ?? `stream_speaker_${Date.now().toString(16)}`;
    if (this.outputStates.has(id)) return this.outputStates.get(id);
    const sink = this.speaker.sink;
    await sink.prepare({codec, sampleRate, channels});
    const state = {
      sessionId,
      streamId: id,
      streamType,
      buffer: new SpeakerPlaybackBuffer({configuration: this.speaker.buffer, sink}),
      ready: false,
      started: false,
      draining: false,
      finishAbort: false,
      appendedLastSeq: -1,
    };
    this.outputStates.set(id, state);
    await this.markOutputReady(state);
    return state;
  }

  async markOutputReady(state) {
    if (state.ready) return;
    state.ready = true;
    await this.sendEvent("stream.output.ready", {
      stream_type: state.streamType,
      reason: "javascript_device_ready",
    }, {sessionId: state.sessionId, streamId: state.streamId, streamType: state.streamType});
  }

  async handlePlaybackActions(streamId, actions) {
    const state = this.outputStates.get(streamId);
    for (const action of actions) {
      if (action.type === "started" && !state.started) {
        state.started = true;
        await this.sendEvent("stream.output.started", {
          stream_type: state.streamType,
          buffered_ms: action.bufferedMs,
        }, {sessionId: state.sessionId, streamId: state.streamId, streamType: state.streamType});
      }
      if (action.type === "pause") {
        await this.sendEvent("downstream.pause.requested", {
          stream_type: state.streamType,
          buffered_ms: action.bufferedMs,
          high_watermark_ms: action.highWatermarkMs,
          reason: "speaker_buffer_high",
        }, {sessionId: state.sessionId, streamId: state.streamId, streamType: state.streamType});
      }
      if (action.type === "resume") {
        await this.sendEvent("downstream.resume.requested", {
          stream_type: state.streamType,
          buffered_ms: action.bufferedMs,
          low_watermark_ms: action.lowWatermarkMs,
          reason: "speaker_buffer_low",
        }, {sessionId: state.sessionId, streamId: state.streamId, streamType: state.streamType});
      }
    }
  }

  startDrainLoop(streamId) {
    const state = this.outputStates.get(streamId);
    if (!state || state.draining) return;
    state.draining = true;
    (async () => {
      while (!this.closed && this.outputStates.has(streamId) && !state.finishAbort) {
        const actions = await state.buffer.drainAvailable();
        await this.handlePlaybackActions(streamId, actions);
        this.diagnostics.speaker = state.buffer.snapshot();
        await sleep(5);
      }
      state.draining = false;
    })();
  }

  async handleConnectionLost(reason) {
    if (this.closed) return;
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
    this.controlReceiveLoopRunning = false;
    this.streamReceiveLoopRunning = false;
    await this.stopMicrophone();
    for (const state of this.outputStates.values()) {
      state.finishAbort = true;
      await state.buffer.cancel();
    }
    this.outputStates.clear();
    this.connectedStreamChannels.clear();
    await this.transport.close();
    this.diagnostics.registered = false;
    this.diagnostics.controlState = "disconnected";
    this.diagnostics.streamState = "disconnected";
    this.diagnostics.lastError = `${reason.type}: ${reason.message}`;
    await this.emitConnectionState({state: "disconnected", reason});
    await this.emitConversationState("waiting");
  }

  emitConnectionState(state) {
    const normalized = typeof state === "string" ? {state} : state;
    this.diagnostics.connectionState = normalized.state;
    for (const handler of this.connectionStateHandlers) handler(normalized);
  }

  emitConversationState(state) {
    this.diagnostics.conversationState = state;
    for (const handler of this.conversationStateHandlers) handler(state);
  }

  async debugLog(message) {
    if (this.logLevel === "silent") return;
    for (const handler of this.debugLogHandlers) await handler(message);
  }

  websocketUrl(path, query = {}) {
    const url = new URL(this.serverUrl);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.pathname = path;
    for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
    return url.toString();
  }

  streamPath(channel) {
    if (channel === StreamChannel.audioInput) return "/ws/stream/audio/input";
    if (channel === StreamChannel.audioOutput) return "/ws/stream/audio/output";
    return "/ws/stream/visual/input";
  }

  streamChannelFor(streamType) {
    if (streamType === "sensor.mic") return StreamChannel.audioInput;
    if (streamType === "actuator.speaker") return StreamChannel.audioOutput;
    return StreamChannel.visualInput;
  }

  nextSeq(streamId) {
    const current = this.sequenceByStream.get(streamId) ?? 0;
    this.sequenceByStream.set(streamId, current + 1);
    return current;
  }

  installAudioInput(audioInput) {
    if (!audioInput.enabled || audioInput.source) return audioInput;
    if (typeof window !== "undefined" && typeof navigator !== "undefined") {
      audioInput.source = new BrowserMicrophoneSource();
    }
    return audioInput;
  }

  installCamera(camera) {
    if (!camera.enabled || camera.source) return camera;
    if (typeof window !== "undefined" && typeof navigator !== "undefined") {
      camera.source = new BrowserCameraFrameSource({videoElement: camera.previewVideoElement});
    }
    return camera;
  }

  installSpeaker(speaker) {
    if (!speaker.enabled || speaker.sink) return speaker;
    if (typeof window !== "undefined" && typeof AudioContext !== "undefined") {
      speaker.sink = new BrowserSpeakerSink();
    } else {
      speaker.sink = new NoopSpeakerSink();
    }
    return speaker;
  }
}
