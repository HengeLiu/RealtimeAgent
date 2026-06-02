import {PROTOCOL_VERSION, nowMs} from "./event.js";

const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

export class StreamChunk {
  /**
   * 构造媒体 stream chunk。
   *
   * 主要逻辑：统一 `sensor.mic`、`sensor.rgb` 和 `actuator.speaker` 的二进制信封。
   * 参数：协议头字段和 payload 字节。
   * 返回值：StreamChunk 实例。
   * 异常情况：缺少必要字段时抛出错误。
   */
  constructor({
    userId,
    sessionId,
    streamId,
    streamType,
    seq,
    payload,
    codec,
    sampleRate,
    channels,
    durationMs,
    timestampMs = nowMs(),
    version = PROTOCOL_VERSION,
    final = false,
    metadata = {},
  }) {
    if (!userId || !sessionId || !streamId || !streamType) {
      throw new Error("missing stream chunk identity fields");
    }
    this.userId = userId;
    this.sessionId = sessionId;
    this.streamId = streamId;
    this.streamType = streamType;
    this.seq = seq;
    this.payload = toUint8Array(payload);
    this.codec = codec;
    this.sampleRate = sampleRate;
    this.channels = channels;
    this.durationMs = durationMs;
    this.timestampMs = timestampMs;
    this.version = version;
    this.final = final;
    this.metadata = metadata;
  }
}

export function toUint8Array(value) {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  return new Uint8Array(value ?? 0);
}

export function encodeStreamChunk(chunk) {
  const normalized = chunk instanceof StreamChunk ? chunk : new StreamChunk(chunk);
  const header = {
    version: normalized.version,
    user_id: normalized.userId,
    session_id: normalized.sessionId,
    stream_id: normalized.streamId,
    stream_type: normalized.streamType,
    seq: normalized.seq,
    timestamp_ms: normalized.timestampMs,
    codec: normalized.codec,
    sample_rate: normalized.sampleRate,
    channels: normalized.channels,
    duration_ms: normalized.durationMs,
    payload_size: normalized.payload.byteLength,
    final: normalized.final,
    metadata: normalized.metadata,
  };
  const headerBytes = textEncoder.encode(JSON.stringify(header));
  const output = new Uint8Array(4 + headerBytes.byteLength + normalized.payload.byteLength);
  const view = new DataView(output.buffer);
  view.setUint32(0, headerBytes.byteLength, false);
  output.set(headerBytes, 4);
  output.set(normalized.payload, 4 + headerBytes.byteLength);
  return output;
}

export function decodeStreamChunk(data) {
  const bytes = toUint8Array(data);
  if (bytes.byteLength < 4) {
    throw new Error("stream chunk 格式错误：message too short");
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const headerLength = view.getUint32(0, false);
  const headerEnd = 4 + headerLength;
  if (headerLength <= 0 || headerEnd > bytes.byteLength) {
    throw new Error("stream chunk 格式错误：invalid header length");
  }
  const header = JSON.parse(textDecoder.decode(bytes.slice(4, headerEnd)));
  const payload = bytes.slice(headerEnd);
  if (header.payload_size !== payload.byteLength) {
    throw new Error("stream chunk 格式错误：payload_size mismatch");
  }
  return new StreamChunk({
    userId: requiredString(header, "user_id"),
    sessionId: requiredString(header, "session_id"),
    streamId: requiredString(header, "stream_id"),
    streamType: requiredString(header, "stream_type"),
    seq: requiredNumber(header, "seq"),
    payload,
    codec: requiredString(header, "codec"),
    sampleRate: requiredNumber(header, "sample_rate"),
    channels: requiredNumber(header, "channels"),
    durationMs: requiredNumber(header, "duration_ms"),
    timestampMs: requiredNumber(header, "timestamp_ms"),
    version: header.version ?? PROTOCOL_VERSION,
    final: Boolean(header.final),
    metadata: header.metadata ?? {},
  });
}

function requiredString(header, key) {
  const value = header[key];
  if (typeof value !== "string") throw new Error(`stream chunk 格式错误：missing ${key}`);
  return value;
}

function requiredNumber(header, key) {
  const value = header[key];
  if (typeof value !== "number") throw new Error(`stream chunk 格式错误：missing ${key}`);
  return value;
}
