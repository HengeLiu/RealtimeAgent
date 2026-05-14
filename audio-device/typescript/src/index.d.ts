export declare const PROTOCOL_VERSION: "audio-chat.v1";
export declare function newId(prefix: string): string;
export declare function nowMs(): number;
export declare function validateEventName(eventName: string): void;
export declare function wsUrl(serverUrl: string, path: string, query?: Record<string, string>): string;

export declare class AudioChatEvent {
  constructor(args: {
    eventName: string;
    userId: string;
    producerId: string;
    payload?: Record<string, unknown>;
    version?: string;
    eventId?: string;
    timestampMs?: number;
    sessionId?: string | null;
    streamId?: string | null;
    streamType?: string | null;
  });
  toObject(): Record<string, unknown>;
  toJson(): string;
  static fromObject(data: Record<string, unknown>): AudioChatEvent;
}

export declare class StreamChunk {
  constructor(args: {
    userId: string;
    sessionId: string;
    streamId: string;
    streamType: string;
    seq: number;
    payload: Uint8Array | ArrayBuffer;
    codec?: string;
    sampleRate?: number;
    channels?: number;
    durationMs?: number;
    timestampMs?: number;
    version?: string;
    final?: boolean;
    metadata?: Record<string, unknown>;
  });
}

export declare class StreamChunkCodec {
  static encodeHeader(header: Record<string, unknown>, payload: Uint8Array | ArrayBuffer): Uint8Array;
  static decodeHeader(raw: Uint8Array | ArrayBuffer): Record<string, unknown> & { payload: Uint8Array };
  static encode(chunk: StreamChunk): Uint8Array;
  static decode(raw: Uint8Array | ArrayBuffer): StreamChunk;
}

export declare class DeviceBuilder {
  static define(deviceId: string): DeviceBuilder;
  user(userId: string): DeviceBuilder;
  name(name: string): DeviceBuilder;
  role(role: string): DeviceBuilder;
  runtime(value: Record<string, string>): DeviceBuilder;
  property(key: string, value: unknown): DeviceBuilder;
  sensorRgb(options?: Record<string, unknown>): DeviceBuilder;
  actuatorVibrator(commands?: string[]): DeviceBuilder;
  supports(): Record<string, unknown>;
  registrationPayload(): Record<string, unknown>;
}
