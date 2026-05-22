import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { StreamChunk, StreamChunkCodec } from "../src/index.js";

const root = resolve("../..");

test("StreamChunkCodec reads golden fixture", () => {
  const header = JSON.parse(readFileSync(resolve(root, "protocol/data/fixtures/streams/rgb-header.json"), "utf8"));
  const raw = readFileSync(resolve(root, "protocol/data/fixtures/streams/rgb-chunk.bin"));
  const decoded = StreamChunkCodec.decodeHeader(raw);

  for (const [key, value] of Object.entries(header)) {
    if (key === "metadata") assert.deepEqual(decoded[key], value);
    else assert.equal(decoded[key], value);
  }
  assert.equal(Buffer.from(decoded.payload).toString("utf8"), "abc");
});

test("StreamChunkCodec round trips object", () => {
  const chunk = new StreamChunk({
    userId: "user-001",
    sessionId: "dev-001",
    streamId: "stream-001",
    streamType: "sensor.rgb",
    seq: 0,
    payload: new TextEncoder().encode("abc"),
    codec: "jpeg",
    sampleRate: 1,
    channels: 1,
    durationMs: 0,
    final: true,
  });
  const decoded = StreamChunkCodec.decode(StreamChunkCodec.encode(chunk));
  assert.equal(decoded.streamId, "stream-001");
  assert.equal(new TextDecoder().decode(decoded.payload), "abc");
});
