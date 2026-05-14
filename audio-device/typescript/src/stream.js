import { PROTOCOL_VERSION, nowMs } from "./events.js";

const encoder = new TextEncoder();
const decoder = new TextDecoder();

export class StreamChunk {
  constructor({ userId, sessionId, streamId, streamType, seq, payload, codec = "pcm16le", sampleRate = 16000, channels = 1, durationMs = 20, timestampMs = nowMs(), version = PROTOCOL_VERSION, final = false, metadata = {} }) {
    this.userId = userId;
    this.sessionId = sessionId;
    this.streamId = streamId;
    this.streamType = streamType;
    this.seq = seq;
    this.payload = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
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

export class StreamChunkCodec {
  static encodeHeader(header, payload) {
    const bytes = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
    const headerBytes = encoder.encode(JSON.stringify({ ...header, payload_size: bytes.byteLength }));
    const out = new Uint8Array(4 + headerBytes.byteLength + bytes.byteLength);
    new DataView(out.buffer).setUint32(0, headerBytes.byteLength, false);
    out.set(headerBytes, 4);
    out.set(bytes, 4 + headerBytes.byteLength);
    return out;
  }

  static decodeHeader(raw) {
    const bytes = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
    if (bytes.byteLength < 4) throw new Error("StreamChunk message too short");
    const headerLength = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint32(0, false);
    const headerEnd = 4 + headerLength;
    if (headerLength <= 0 || headerEnd > bytes.byteLength) throw new Error("StreamChunk header length is invalid");
    const header = JSON.parse(decoder.decode(bytes.slice(4, headerEnd)));
    const payload = bytes.slice(headerEnd);
    if (payload.byteLength !== Number(header.payload_size ?? -1)) throw new Error("StreamChunk payload_size mismatch");
    return { ...header, payload };
  }

  static encode(chunk) {
    return this.encodeHeader({
      version: chunk.version,
      user_id: chunk.userId,
      session_id: chunk.sessionId,
      stream_id: chunk.streamId,
      stream_type: chunk.streamType,
      seq: chunk.seq,
      timestamp_ms: chunk.timestampMs,
      codec: chunk.codec,
      sample_rate: chunk.sampleRate,
      channels: chunk.channels,
      duration_ms: chunk.durationMs,
      final: chunk.final,
      metadata: chunk.metadata,
    }, chunk.payload);
  }

  static decode(raw) {
    const data = this.decodeHeader(raw);
    return new StreamChunk({
      version: data.version ?? PROTOCOL_VERSION,
      userId: data.user_id,
      sessionId: data.session_id,
      streamId: data.stream_id,
      streamType: data.stream_type,
      seq: Number(data.seq),
      timestampMs: Number(data.timestamp_ms),
      codec: data.codec,
      sampleRate: Number(data.sample_rate),
      channels: Number(data.channels),
      durationMs: Number(data.duration_ms),
      final: Boolean(data.final),
      metadata: data.metadata ?? {},
      payload: data.payload,
    });
  }
}
