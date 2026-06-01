import { RealtimeAgentEvent } from "./events.js";
import { StreamChunkCodec } from "./stream.js";

export function wsUrl(serverUrl, path, query = {}) {
  const url = new URL(serverUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = path;
  url.search = new URLSearchParams(query).toString();
  return url.toString();
}

export class RealtimeAgentDeviceClient {
  constructor({ serverUrl, device, WebSocketImpl = globalThis.WebSocket }) {
    this.serverUrl = serverUrl.replace(/\/$/, "");
    this.device = device;
    this.devicePayload = typeof device.registrationPayload === "function" ? device.registrationPayload() : device;
    this.userId = device.userId ?? this.devicePayload.user_id;
    this.deviceId = this.devicePayload.device_id;
    this.WebSocketImpl = WebSocketImpl;
    this.controlWs = null;
    this.streamWsByType = new Map();
  }

  event(eventName, payload = {}, extra = {}) {
    return new RealtimeAgentEvent({
      eventName,
      userId: this.userId,
      producerId: this.deviceId,
      payload,
      ...extra,
    });
  }

  connectControl() {
    this.controlWs = new this.WebSocketImpl(wsUrl(this.serverUrl, "/ws/control"));
    return this.controlWs;
  }

  sendEvent(event) {
    this.controlWs.send(event.toJson ? event.toJson() : JSON.stringify(event));
  }

  streamPath(streamType) {
    if (streamType === "sensor.mic") return "/ws/stream/audio/input";
    if (streamType === "actuator.speaker") return "/ws/stream/audio/output";
    if (streamType === "sensor.rgb") return "/ws/stream/visual/input";
    throw new Error(`unsupported stream_type for media websocket: ${streamType}`);
  }

  ensureStream(streamType = "sensor.rgb") {
    let streamWs = this.streamWsByType.get(streamType);
    if (!streamWs || streamWs.readyState > 1) {
      streamWs = new this.WebSocketImpl(wsUrl(this.serverUrl, this.streamPath(streamType), { device_id: this.deviceId }));
      this.streamWsByType.set(streamType, streamWs);
    }
    return streamWs;
  }

  sendStreamChunk(chunk) {
    this.ensureStream(chunk.streamType).send(StreamChunkCodec.encode(chunk));
  }
}
