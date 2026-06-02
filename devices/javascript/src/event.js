export const PROTOCOL_VERSION = "realtime-agent.v1";

export function makeId(prefix) {
  const random =
    globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 12) ??
    Math.random().toString(16).slice(2, 14).padEnd(12, "0");
  return `${prefix}_${random.toLowerCase()}`;
}

export function nowMs() {
  return Date.now();
}

/**
 * 创建标准控制事件。
 *
 * 主要逻辑：把 JavaScript 驼峰参数转换为协议使用的 snake_case 字段。
 * 参数：事件名、用户、生产者、payload 以及可选 session / stream 信息。
 * 返回值：可直接 JSON.stringify 的事件对象。
 * 异常情况：缺少 eventName、userId 或 producerId 时抛出错误。
 */
export function createEvent({
  eventName,
  userId,
  producerId,
  payload = {},
  sessionId = undefined,
  streamId = undefined,
  streamType = undefined,
  version = PROTOCOL_VERSION,
  eventId = makeId("evt"),
  timestampMs = nowMs(),
}) {
  if (!eventName || !userId || !producerId) {
    throw new Error("missing eventName/userId/producerId");
  }
  const event = {
    version,
    event_id: eventId,
    event_name: eventName,
    timestamp_ms: timestampMs,
    user_id: userId,
    producer_id: producerId,
    payload,
  };
  if (sessionId) event.session_id = sessionId;
  if (streamId) event.stream_id = streamId;
  if (streamType) event.stream_type = streamType;
  return event;
}

export function parseEvent(textOrObject) {
  const event = typeof textOrObject === "string" ? JSON.parse(textOrObject) : textOrObject;
  if (!event?.event_name || !event?.user_id || !event?.producer_id) {
    throw new Error("事件格式错误：missing event_name/user_id/producer_id");
  }
  return event;
}

export function eventName(event) {
  return event.event_name;
}

export function eventPayload(event) {
  return event.payload ?? {};
}

export function eventSessionId(event) {
  return event.session_id;
}

export function eventStreamId(event) {
  return event.stream_id;
}

export function eventStreamType(event) {
  return event.stream_type ?? event.payload?.stream_type;
}
