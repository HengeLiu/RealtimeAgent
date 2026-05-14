import { AudioChatEvent } from "./events.js";
import { StreamChunkCodec } from "./stream.js";

export function wsUrl(serverUrl, path, query = {}) {
  const url = new URL(serverUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = path;
  url.search = new URLSearchParams(query).toString();
  return url.toString();
}

export class AudioChatDeviceClient {
  constructor({ serverUrl, device, WebSocketImpl = globalThis.WebSocket }) {
    this.serverUrl = serverUrl.replace(/\/$/, "");
    this.device = device;
    this.devicePayload = typeof device.registrationPayload === "function" ? device.registrationPayload() : device;
    this.userId = device.userId ?? this.devicePayload.user_id;
    this.deviceId = this.devicePayload.device_id;
    this.WebSocketImpl = WebSocketImpl;
    this.controlWs = null;
    this.streamWs = null;
  }

  event(eventName, payload = {}, extra = {}) {
    return new AudioChatEvent({
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

  ensureStream() {
    if (!this.streamWs || this.streamWs.readyState > 1) {
      this.streamWs = new this.WebSocketImpl(wsUrl(this.serverUrl, "/ws/stream", { device_id: this.deviceId }));
    }
    return this.streamWs;
  }

  sendStreamChunk(chunk) {
    this.ensureStream().send(StreamChunkCodec.encode(chunk));
  }
}
