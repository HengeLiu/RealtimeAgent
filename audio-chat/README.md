# audio-chat

`audio-chat` is the new server-side Python SDK for stream-based voice sessions.

This first phase implements the minimal playback loop described in
`docs/audio-chat-sdk-architecture.md`:

1. device registration and event subscriptions;
2. `sensor.mic` input stream ingestion;
3. mock `TextAgentCore`;
4. mock Streaming TTS;
5. `actuator.speaker` output stream delivery through Playback Arbiter.

## Local development

Install the SDK from this repository:

```bash
uv sync --python 3.11
uv pip install -e audio-chat
```

Run the current minimal checks:

```bash
uv run audio-chat.dev.preflight --report audio-chat/runs/preflight.json
uv run audio-chat.playback.glass --config audio-chat/examples/minimal/playback.yaml
```

The startup and multi-endpoint development plan is documented in
`docs/audio-chat-sdk-architecture.md`, section 15, "安装、启动与研发联调".
