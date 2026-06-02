import assert from "node:assert/strict";
import test from "node:test";
import {DeviceClient} from "../src/device-client.js";
import {buildRegistrationPayload} from "../src/device-profile.js";
import {createEvent} from "../src/event.js";
import {AudioInput, Camera, PlaybackBuffer, Speaker} from "../src/options.js";
import {decodeStreamChunk, encodeStreamChunk, StreamChunk} from "../src/stream-chunk.js";
import {NoopSpeakerSink} from "../src/media/noop-speaker-sink.js";
import {SpeakerPlaybackBuffer} from "../src/media/speaker-playback-buffer.js";
import {StreamChannel} from "../src/transport/browser-websocket-transport.js";

class MockTransport {
  constructor() {
    this.controlConnectedUrl = "";
    this.streamConnectedUrls = new Map();
    this.sentControlTexts = [];
    this.controlInbox = [];
    this.sentStreamData = [];
    this.sentStreamDataByChannel = new Map();
    this.streamInboxByChannel = new Map();
    this.closed = false;
  }

  async connectControl(url) {
    this.controlConnectedUrl = url;
  }

  async connectStream(channel, url) {
    this.streamConnectedUrls.set(channel, url);
  }

  async sendControl(text) {
    this.sentControlTexts.push(text);
  }

  async receiveControl() {
    if (this.controlInbox.length === 0) throw new Error("empty control inbox");
    return this.controlInbox.shift();
  }

  async sendStream(data, channel) {
    this.sentStreamData.push(data);
    const list = this.sentStreamDataByChannel.get(channel) ?? [];
    list.push(data);
    this.sentStreamDataByChannel.set(channel, list);
  }

  async receiveStream(channel) {
    const list = this.streamInboxByChannel.get(channel) ?? [];
    if (list.length === 0) throw new Error("empty stream inbox");
    return list.shift();
  }

  async close() {
    this.closed = true;
  }
}

class AsyncMockTransport extends MockTransport {
  constructor() {
    super();
    this.controlWaiters = [];
  }

  pushControl(eventText) {
    const waiter = this.controlWaiters.shift();
    if (waiter) waiter.resolve(eventText);
    else this.controlInbox.push(eventText);
  }

  async receiveControl() {
    if (this.controlInbox.length > 0) return this.controlInbox.shift();
    return new Promise((resolve, reject) => {
      this.controlWaiters.push({resolve, reject});
    });
  }

  async close() {
    await super.close();
    const error = new Error("transport closed");
    for (const waiter of this.controlWaiters.splice(0)) waiter.reject(error);
  }
}

class BlockingDrainSink extends NoopSpeakerSink {
  constructor() {
    super();
    this.drainStarted = false;
    this.drainResolved = false;
    this._resolveDrain = null;
    this.drainStartedPromise = new Promise((resolve) => {
      this._notifyDrainStarted = resolve;
    });
  }

  async drain() {
    this.drainStarted = true;
    this._notifyDrainStarted();
    await new Promise((resolve) => {
      this._resolveDrain = resolve;
    });
    this.drainResolved = true;
  }

  async cancel() {
    await super.cancel();
    this._resolveDrain?.();
  }
}

function eventJSON(eventName, options = {}) {
  return JSON.stringify(createEvent({
    eventName,
    userId: "user-001",
    producerId: "server-main",
    timestampMs: 1,
    ...options,
  }));
}

function sentEvents(transport) {
  return transport.sentControlTexts.map((text) => JSON.parse(text));
}

test("StreamChunk 编解码能保留 header 与 payload", () => {
  const chunk = new StreamChunk({
    userId: "user-001",
    sessionId: "session-001",
    streamId: "stream-rgb-001",
    streamType: "sensor.rgb",
    seq: 3,
    payload: new TextEncoder().encode("abc"),
    codec: "jpeg",
    sampleRate: 1,
    channels: 1,
    durationMs: 0,
    final: true,
    metadata: {request_id: "req-001"},
  });

  const decoded = decodeStreamChunk(encodeStreamChunk(chunk));

  assert.equal(decoded.streamType, "sensor.rgb");
  assert.equal(decoded.seq, 3);
  assert.equal(new TextDecoder().decode(decoded.payload), "abc");
  assert.equal(decoded.metadata.request_id, "req-001");
});

test("StreamChunk payload_size 不一致时会拒绝", () => {
  const chunk = new StreamChunk({
    userId: "user-001",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    seq: 0,
    payload: new Uint8Array([1, 2, 3]),
    codec: "pcm16le",
    sampleRate: 24000,
    channels: 1,
    durationMs: 20,
  });
  const encoded = encodeStreamChunk(chunk).slice(0, -1);

  assert.throws(() => decodeStreamChunk(encoded), /payload_size mismatch/);
});

test("注册 payload 会根据启用硬件生成 properties 与 supports", () => {
  const payload = buildRegistrationPayload({
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    clientType: "web-chat",
    audioInput: AudioInput.enabled({source: {}}),
    camera: Camera.enabled({source: {}}),
    speaker: Speaker.enabled({sink: new NoopSpeakerSink()}),
    customCommands: ["demo.ping"],
    customEventSubscriptions: ["custom.demo.message"],
  });

  assert.equal(payload.properties["realtime_agent.audio_input"], "sensor.mic");
  assert.equal(payload.properties["realtime_agent.audio_output"], "actuator.speaker");
  assert.equal(payload.properties["realtime_agent.custom_command_consumer"], true);
  assert.deepEqual(payload.properties["realtime_agent.custom_commands"], ["demo.ping"]);
  assert.equal(payload.supports.sensors[0].type, "rgb");
});

test("client register 会连接 control 并发送注册事件", async () => {
  const transport = new MockTransport();
  transport.controlInbox.push(eventJSON("control.device.registered", {
    payload: {device_id: "dev-web-001", connection_id: "conn-001", heartbeat_interval_seconds: 60},
  }));
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    transport,
  });

  const registered = await client.register({startHeartbeat: false});

  assert.equal(registered.event_name, "control.device.registered");
  assert.equal(transport.controlConnectedUrl, "ws://127.0.0.1:8765/ws/control");
  assert.equal(sentEvents(transport)[0].event_name, "control.device.register.requested");
  assert.equal(client.diagnosticsSnapshot().registered, true);
});

test("startConversation 会注册、准备 stream 并发送 wake", async () => {
  const transport = new MockTransport();
  transport.controlInbox.push(eventJSON("control.device.registered", {
    payload: {device_id: "dev-web-001", connection_id: "conn-001", heartbeat_interval_seconds: 60},
  }));
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    audioInput: AudioInput.enabled({source: {start: async () => {}, stop: async () => {}}}),
    speaker: Speaker.enabled({sink: new NoopSpeakerSink()}),
    transport,
  });

  await client.startConversation({reason: "unit_start"});

  assert.deepEqual(sentEvents(transport).map((event) => event.event_name), [
    "control.device.register.requested",
    "control.user.wake.detected",
  ]);
  assert.equal(transport.streamConnectedUrls.get(StreamChannel.audioInput), "ws://127.0.0.1:8765/ws/stream/audio/input?device_id=dev-web-001");
  assert.equal(transport.streamConnectedUrls.get(StreamChannel.audioOutput), "ws://127.0.0.1:8765/ws/stream/audio/output?device_id=dev-web-001");
  await client.close({force: true});
});

test("requestConversationClose 在 active 状态只发送端侧关闭请求", async () => {
  const transport = new MockTransport();
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    audioInput: AudioInput.enabled({source: {start: async () => {}, stop: async () => {}}}),
    transport,
  });
  await client.dispatchEvent(createEvent({
    eventName: "control.audio_session.open.requested",
    userId: "user-001",
    producerId: "server-main",
    sessionId: "session-001",
  }));

  await client.requestConversationClose({reason: "unit_test"});

  assert.equal(sentEvents(transport).at(-1).event_name, "control.user.dialog.close.requested");
  assert.equal(sentEvents(transport).at(-1).payload.reason, "unit_test");
});

test("audio session open 会启动麦克风 source 并上传 sensor.mic", async () => {
  const transport = new MockTransport();
  const source = {
    async start({onChunk}) {
      onChunk(new Uint8Array(640));
    },
    async stop() {},
  };
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    audioInput: AudioInput.enabled({source}),
    transport,
  });

  await client.dispatchEvent(createEvent({
    eventName: "control.audio_session.open.requested",
    userId: "user-001",
    producerId: "server-main",
    sessionId: "session-001",
  }));
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.equal(sentEvents(transport)[0].event_name, "control.audio_session.opened");
  const chunk = decodeStreamChunk(transport.sentStreamData[0]);
  assert.equal(chunk.streamType, "sensor.mic");
  assert.equal(chunk.sessionId, "session-001");
});

test("custom command 会调用 App handler 并通过 context 发送 custom 结果", async () => {
  const transport = new MockTransport();
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    transport,
  });
  client.onCustomCommand("demo.ping", async (context) => {
    await context.emit("custom.demo.pong", {ok: context.payload.ok});
  });

  await client.dispatchEvent(createEvent({
    eventName: "custom.command.requested",
    userId: "user-001",
    producerId: "server-main",
    payload: {command: "demo.ping", payload: {ok: true}},
  }));

  assert.equal(sentEvents(transport)[0].event_name, "custom.demo.pong");
  assert.equal(sentEvents(transport)[0].payload.ok, true);
});

test("标准事件不会触发 onEvent handler", async () => {
  const transport = new MockTransport();
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    speaker: Speaker.enabled({sink: new NoopSpeakerSink()}),
    transport,
  });
  let called = false;
  client.onEvent("custom.demo.message", async () => { called = true; });

  await client.dispatchEvent(createEvent({
    eventName: "stream.output.start.requested",
    userId: "user-001",
    producerId: "server-main",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
  }));

  assert.equal(called, false);
});

test("sensor.rgb 请求会通过视觉上行发送单帧", async () => {
  const transport = new MockTransport();
  const source = {
    async readFrame() {
      return {
        payload: new TextEncoder().encode("jpg"),
        codec: "jpeg",
        metadata: {width: 10, height: 10},
      };
    },
  };
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    camera: Camera.enabled({source}),
    transport,
  });

  await client.dispatchEvent(createEvent({
    eventName: "stream.control.open.requested",
    userId: "user-001",
    producerId: "server-main",
    sessionId: "session-001",
    streamId: "stream-rgb-001",
    streamType: "sensor.rgb",
    payload: {stream_type: "sensor.rgb", request_id: "req-001"},
  }));

  assert.deepEqual(sentEvents(transport).map((event) => event.event_name), [
    "stream.input.opened",
    "stream.input.closed",
  ]);
  const openedPayload = sentEvents(transport)[0].payload;
  assert.deepEqual(openedPayload.format, {
    codec: "jpeg",
    sample_rate: 1,
    channels: 1,
    chunk_ms: 0,
  });
  assert.equal(transport.streamConnectedUrls.get(StreamChannel.visualInput), "ws://127.0.0.1:8765/ws/stream/visual/input?device_id=dev-web-001");
  const chunk = decodeStreamChunk(transport.sentStreamData[0]);
  assert.equal(chunk.streamType, "sensor.rgb");
  assert.equal(chunk.codec, openedPayload.format.codec);
});

test("speaker buffer 会按 seq 顺序 drain 乱序 chunk", async () => {
  const sink = new NoopSpeakerSink();
  const buffer = new SpeakerPlaybackBuffer({
    configuration: new PlaybackBuffer({startWatermarkMs: 20}),
    sink,
  });
  const makeChunk = (seq) => new StreamChunk({
    userId: "user-001",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    seq,
    payload: new Uint8Array([seq]),
    codec: "pcm16le",
    sampleRate: 24000,
    channels: 1,
    durationMs: 20,
  });

  await buffer.append(makeChunk(0));
  await buffer.append(makeChunk(2));
  await buffer.append(makeChunk(1));
  await buffer.drainAvailable();

  assert.deepEqual(sink.chunks.map((chunk) => chunk.seq), [0, 1, 2]);
  assert.equal(buffer.snapshot().outOfOrderChunks, 1);
});

test("finish 会等待 output_last_seq 后再发送 finished", async () => {
  const transport = new MockTransport();
  const sink = new NoopSpeakerSink();
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    speaker: Speaker.enabled({buffer: new PlaybackBuffer({startWatermarkMs: 20}), sink}),
    transport,
  });
  const first = new StreamChunk({
    userId: "user-001",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    seq: 0,
    payload: new Uint8Array([0]),
    codec: "pcm16le",
    sampleRate: 24000,
    channels: 1,
    durationMs: 20,
  });
  const second = new StreamChunk({...first, seq: 1, payload: new Uint8Array([1])});

  await client.dispatchStreamChunk(first);
  const finish = client.dispatchEvent(createEvent({
    eventName: "stream.output.finish.requested",
    userId: "user-001",
    producerId: "server-main",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    payload: {stream_type: "actuator.speaker", output_last_seq: 1},
  }));
  await finish;
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(sentEvents(transport).some((event) => event.event_name === "stream.output.finished"), false);

  await client.dispatchStreamChunk(second);
  await client.outputStates.get("stream-speaker-001")?.finishTask;

  assert.deepEqual(sink.chunks.map((chunk) => chunk.seq), [0, 1]);
  assert.equal(sentEvents(transport).some((event) => event.event_name === "stream.output.finished"), true);
});

test("finish drain 期间 control loop 仍能处理 cancel", async () => {
  const transport = new MockTransport();
  const sink = new BlockingDrainSink();
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    speaker: Speaker.enabled({buffer: new PlaybackBuffer({startWatermarkMs: 20}), sink}),
    transport,
  });
  const chunk = new StreamChunk({
    userId: "user-001",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    seq: 0,
    payload: new Uint8Array([0]),
    codec: "pcm16le",
    sampleRate: 24000,
    channels: 1,
    durationMs: 20,
  });

  await client.dispatchStreamChunk(chunk);
  await client.dispatchEvent(createEvent({
    eventName: "stream.output.finish.requested",
    userId: "user-001",
    producerId: "server-main",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    payload: {stream_type: "actuator.speaker", output_last_seq: 0},
  }));
  await sink.drainStartedPromise;
  assert.equal(sentEvents(transport).some((event) => event.event_name === "stream.output.finished"), false);

  await client.dispatchEvent(createEvent({
    eventName: "stream.output.cancel.requested",
    userId: "user-001",
    producerId: "server-main",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    payload: {stream_type: "actuator.speaker"},
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  const events = sentEvents(transport).map((event) => event.event_name);
  assert.equal(sink.cancelCalled, true);
  assert.equal(events.includes("stream.output.cancelled"), true);
  assert.equal(events.includes("stream.output.finished"), false);
});

test("control receive loop 不会被 finish drain 阻塞", async () => {
  const transport = new AsyncMockTransport();
  const sink = new BlockingDrainSink();
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    speaker: Speaker.enabled({buffer: new PlaybackBuffer({startWatermarkMs: 20}), sink}),
    transport,
  });
  const chunk = new StreamChunk({
    userId: "user-001",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    seq: 0,
    payload: new Uint8Array([0]),
    codec: "pcm16le",
    sampleRate: 24000,
    channels: 1,
    durationMs: 20,
  });

  await client.dispatchStreamChunk(chunk);
  client.startControlReceiveLoop();
  transport.pushControl(eventJSON("stream.output.finish.requested", {
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    payload: {stream_type: "actuator.speaker", output_last_seq: 0},
  }));
  await sink.drainStartedPromise;
  transport.pushControl(eventJSON("stream.output.cancel.requested", {
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    payload: {stream_type: "actuator.speaker"},
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  await client.close({force: true});

  const events = sentEvents(transport).map((event) => event.event_name);
  assert.equal(sink.cancelCalled, true);
  assert.equal(events.includes("stream.output.cancelled"), true);
  assert.equal(events.includes("stream.output.finished"), false);
});

test("cancel 会抢占 pending finish 并清空播放", async () => {
  const transport = new MockTransport();
  const sink = new NoopSpeakerSink();
  const client = new DeviceClient({
    serverUrl: "http://127.0.0.1:8765",
    deviceId: "dev-web-001",
    userId: "user-001",
    name: "Web Chat",
    speaker: Speaker.enabled({sink}),
    transport,
  });

  await client.dispatchEvent(createEvent({
    eventName: "stream.output.cancel.requested",
    userId: "user-001",
    producerId: "server-main",
    sessionId: "session-001",
    streamId: "stream-speaker-001",
    streamType: "actuator.speaker",
    payload: {stream_type: "actuator.speaker"},
  }));

  assert.equal(sentEvents(transport)[0].event_name, "stream.output.cancelled");
});
