import assert from "node:assert/strict";
import test from "node:test";
import { AudioChatEvent, DeviceBuilder, wsUrl } from "../src/index.js";

test("DeviceBuilder creates structured supports registration payload", () => {
  const payload = DeviceBuilder.define("dev-js-001")
    .user("user-001")
    .name("Browser")
    .role("glass")
    .sensorRgb({ modes: ["single"], format: "jpeg", frequencyHz: 1 })
    .actuatorVibrator(["vibrate"])
    .registrationPayload();

  assert.equal(payload.device_id, "dev-js-001");
  assert.equal(payload.properties.device_role, "glass");
  assert.equal(payload.supports.sensors[0].type, "rgb");
});

test("AudioChatEvent serializes envelope fields", () => {
  const event = new AudioChatEvent({
    eventName: "command.completed",
    userId: "user-001",
    producerId: "dev-js-001",
    payload: { command_id: "cmd-001" },
  }).toObject();

  assert.equal(event.version, "audio-chat.v1");
  assert.equal(event.event_name, "command.completed");
  assert.equal(event.payload.command_id, "cmd-001");
});

test("wsUrl converts http URLs to websocket URLs", () => {
  assert.equal(wsUrl("http://127.0.0.1:8765", "/ws/control"), "ws://127.0.0.1:8765/ws/control");
  assert.equal(wsUrl("https://example.test", "/ws/stream", { device_id: "d1" }), "wss://example.test/ws/stream?device_id=d1");
});
