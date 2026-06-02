export function buildRegistrationPayload({
  deviceId,
  userId,
  name,
  clientType = "javascript",
  sdkVersion = "realtime-agent-javascript-device-sdk-0.1.0",
  runtime = defaultRuntime(),
  properties = {},
  auth = null,
  audioInput,
  camera,
  speaker,
  customCommands = [],
  customEventSubscriptions = [],
}) {
  if (!deviceId || !userId) {
    throw new Error("deviceId and userId are required");
  }
  const props = {...properties};
  const sensors = [];
  const actuators = [];

  if (audioInput?.enabled) {
    props["realtime_agent.audio_input"] = audioInput.configuration.streamType;
    props["realtime_agent.audio_input.format"] = {
      codec: audioInput.configuration.codec,
      sample_rate: audioInput.configuration.sampleRate,
      channels: audioInput.configuration.channels,
      chunk_ms: audioInput.configuration.chunkMs,
    };
  }

  if (speaker?.enabled) {
    props["realtime_agent.audio_output"] = "actuator.speaker";
    props["realtime_agent.audio_output.duplex_mode"] = speaker.duplexMode;
    props["realtime_agent.audio_output.buffer"] = {
      start_watermark_ms: speaker.buffer.startWatermarkMs,
      low_watermark_ms: speaker.buffer.lowWatermarkMs,
      high_watermark_ms: speaker.buffer.highWatermarkMs,
      max_buffer_ms: speaker.buffer.maxBufferMs,
    };
  }

  if (camera?.enabled) {
    sensors.push({
      type: "rgb",
      modes: camera.modes,
      default: {
        format: camera.format,
        sample_count: 1,
      },
    });
  }

  if (customCommands.length > 0) {
    props["realtime_agent.custom_command_consumer"] = true;
    props["realtime_agent.custom_commands"] = [...customCommands].sort();
  }
  if (customEventSubscriptions.length > 0) {
    props["realtime_agent.custom_event_subscriptions"] = [...customEventSubscriptions].sort();
  }

  const payload = {
    device_id: deviceId,
    name,
    device_name: name,
    client_type: clientType,
    sdk_version: sdkVersion,
    runtime,
    properties: props,
    supports: {},
  };
  if (sensors.length > 0) payload.supports.sensors = sensors;
  if (actuators.length > 0) payload.supports.actuators = actuators;
  if (auth) payload.auth = auth;
  return payload;
}

function defaultRuntime() {
  const isBrowser = typeof window !== "undefined" && typeof navigator !== "undefined";
  return {
    platform: isBrowser ? "browser" : "node",
    language: "javascript",
    user_agent: isBrowser ? navigator.userAgent : `node ${globalThis.process?.version ?? ""}`.trim(),
  };
}
