export const PROTOCOL_VERSION = "audio-chat.v1";
const FORBIDDEN_EVENT_FIELDS = new Set(["target_device", "target_device_id", "source_device", "source_device_id"]);
const MEDIA_PAYLOAD_KEYS = new Set([
  "audio",
  "audio_bytes",
  "audio_base64",
  "image",
  "image_bytes",
  "image_base64",
  "video",
  "video_bytes",
  "video_base64",
  "media",
  "media_bytes",
  "media_base64",
  "payload_bytes",
  "raw_bytes",
]);
const MAX_CONTROL_PAYLOAD_TEXT_CHARS = 16384;

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

export function validateEventEnvelope(data) {
  for (const key of Object.keys(data ?? {})) {
    if (FORBIDDEN_EVENT_FIELDS.has(key)) throw new Error(`event envelope contains forbidden device routing field: ${key}`);
  }
  validateEventName(data?.event_name);
  const payload = data?.payload ?? {};
  if (payload === null || Array.isArray(payload) || typeof payload !== "object") throw new Error("event payload must be an object");
  for (const key of Object.keys(payload)) {
    if (FORBIDDEN_EVENT_FIELDS.has(key)) throw new Error(`event payload contains forbidden device routing field: ${key}`);
  }
  validateControlEventPayload(payload);
}

export function validateControlEventPayload(payload) {
  const walk = (value, path) => {
    const key = path.split(".").pop().replace(/\[\d+\]$/, "");
    if (MEDIA_PAYLOAD_KEYS.has(key)) throw new Error(`control event payload must not contain media bytes: ${path}`);
    if (value instanceof Uint8Array || value instanceof ArrayBuffer) throw new Error(`control event payload must not contain bytes: ${path}`);
    if (typeof value === "string" && value.length > MAX_CONTROL_PAYLOAD_TEXT_CHARS) throw new Error(`control event payload text is too large: ${path}`);
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${path}[${index}]`));
      return;
    }
    if (value !== null && typeof value === "object") {
      for (const [childKey, childValue] of Object.entries(value)) {
        walk(childValue, path ? `${path}.${childKey}` : childKey);
      }
    }
  };
  walk(payload, "payload");
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
    validateEventEnvelope(data);
    return data;
  }

  toJson() {
    return JSON.stringify(this.toObject());
  }

  static fromObject(data) {
    validateEventEnvelope(data);
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
