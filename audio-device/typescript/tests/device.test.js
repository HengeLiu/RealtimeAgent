import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";
import { RealtimeAgentEvent, DeviceBuilder, wsUrl } from "../src/index.js";

const root = resolve("../..");

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

test("RealtimeAgentEvent serializes envelope fields", () => {
  const event = new RealtimeAgentEvent({
    eventName: "command.completed",
    userId: "user-001",
    producerId: "dev-js-001",
    payload: { command_id: "cmd-001" },
  }).toObject();

  assert.equal(event.version, "realtime-agent.v1");
  assert.equal(event.event_name, "command.completed");
  assert.equal(event.payload.command_id, "cmd-001");
});

test("RealtimeAgentEvent reads protocol golden fixtures", () => {
  const names = [
    "command-accepted.json",
    "command-completed.json",
    "command-failed.json",
    "command-progress.json",
    "command-requested.json",
    "register-registered.json",
    "register-requested.json",
    "stream-close-requested.json",
    "stream-open-requested.json",
  ];
  for (const name of names) {
    const data = JSON.parse(readFileSync(resolve(root, "testdata/protocol/events", name), "utf8"));
    const event = RealtimeAgentEvent.fromObject(data).toObject();
    assert.equal(event.event_name, data.event_name);
    assert.equal(event.user_id, data.user_id);
    assert.equal(event.producer_id, data.producer_id);
  }
});

test("RealtimeAgentEvent rejects invalid protocol envelope fixtures", () => {
  const names = ["control-payload-media.json", "target-device-routing.json"];
  for (const name of names) {
    const data = JSON.parse(readFileSync(resolve(root, "testdata/protocol/invalid/events", name), "utf8"));
    assert.throws(() => RealtimeAgentEvent.fromObject(data), /forbidden device routing|media bytes/);
  }
});

test("wsUrl converts http URLs to websocket URLs", () => {
  assert.equal(wsUrl("http://127.0.0.1:8765", "/ws/control"), "ws://127.0.0.1:8765/ws/control");
  assert.equal(wsUrl("https://example.test", "/ws/stream", { device_id: "d1" }), "wss://example.test/ws/stream?device_id=d1");
});
