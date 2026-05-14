export const PROTOCOL_VERSION = "audio-chat.v1";

export function nowMs() {
  return Date.now();
}

export function newId(prefix) {
  const random = cryptoRandomHex().slice(0, 12);
  return `${prefix}_${random}`;
}

function cryptoRandomHex() {
  const g = globalThis;
  if (g.crypto && typeof g.crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(8);
    g.crypto.getRandomValues(bytes);
    return Array.from(bytes, (item) => item.toString(16).padStart(2, "0")).join("");
  }
  return Math.random().toString(16).slice(2).padEnd(12, "0");
}

export function validateEventName(eventName) {
  if (typeof eventName !== "string" || !/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/.test(eventName) || eventName.includes("*")) {
    throw new Error(`invalid event_name format: ${eventName}`);
  }
}

export class AudioChatEvent {
  constructor({ eventName, userId, producerId, payload = {}, version = PROTOCOL_VERSION, eventId = newId("evt"), timestampMs = nowMs(), sessionId = null, streamId = null, streamType = null }) {
    this.eventName = eventName;
    this.userId = userId;
    this.producerId = producerId;
    this.payload = payload;
    this.version = version;
    this.eventId = eventId;
    this.timestampMs = timestampMs;
    this.sessionId = sessionId;
    this.streamId = streamId;
    this.streamType = streamType;
  }

  toObject() {
    validateEventName(this.eventName);
    const data = {
      version: this.version,
      event_id: this.eventId,
      event_name: this.eventName,
      timestamp_ms: this.timestampMs,
      user_id: this.userId,
      producer_id: this.producerId,
      payload: this.payload ?? {},
    };
    if (this.sessionId) data.session_id = this.sessionId;
    if (this.streamId) data.stream_id = this.streamId;
    if (this.streamType) data.stream_type = this.streamType;
    return data;
  }

  toJson() {
    return JSON.stringify(this.toObject());
  }

  static fromObject(data) {
    return new AudioChatEvent({
      version: data.version ?? PROTOCOL_VERSION,
      eventId: data.event_id ?? newId("evt"),
      eventName: data.event_name,
      timestampMs: data.timestamp_ms ?? nowMs(),
      userId: data.user_id,
      producerId: data.producer_id,
      sessionId: data.session_id ?? null,
      streamId: data.stream_id ?? null,
      streamType: data.stream_type ?? null,
      payload: data.payload ?? {},
    });
  }
}
