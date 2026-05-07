# audio-chat

`audio-chat` is the new server-side Python SDK for stream-based voice sessions.

This phase implements the minimal network playback loop described in
`docs/audio-chat-sdk-architecture.md`:

1. device registration and event subscriptions;
2. `sensor.mic` input stream ingestion;
3. mock `TextAgentCore`;
4. mock Streaming TTS;
5. `actuator.speaker` output stream delivery through Playback Arbiter;
6. HTTP/WebSocket server transport for control events and stream chunks.

## Local development

Install the SDK from this repository:

```bash
uv sync --python 3.11
uv pip install -e audio-chat
```

Create local developer configs for the basic app example:

```bash
uv run audio-chat.config.sync --app-root audio-chat/examples/basic-app
```

Run the current minimal checks:

```bash
uv run audio-chat.dev.preflight --report audio-chat/runs/preflight.json
```

Run the network SDK loop with two terminals:

```bash
uv run audio-chat.server.run --config audio-chat/examples/minimal/server.yaml
```

```bash
uv run audio-chat.dev.preflight \
  --config audio-chat/examples/minimal/server.yaml \
  --require-server \
  --report audio-chat/runs/preflight-live.json
uv run audio-chat.playback.glass --config audio-chat/examples/minimal/playback.yaml
```

For background development workflows, the SDK also exposes these entry points:

```bash
uv run audio-chat.server.start --config audio-chat/examples/minimal/server.yaml --dry-run
uv run audio-chat.server.logs --log-file audio-chat/runs/audio-chat/server.log
uv run audio-chat.server.stop --dry-run
uv run audio-chat.phone.mock --help
uv run audio-chat.sdk.package-check --report audio-chat/runs/package-check.json
```

Useful debug endpoints:

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/users/user-playback-001
```

The startup and multi-endpoint development plan is documented in
`docs/audio-chat-sdk-architecture.md`, section 15, "安装、启动与研发联调".

## Basic app template

`examples/basic-app` is the copyable developer template for capability work. It
contains:

- `echo_text` Tool: a minimal auto-discovered Tool.
- `sample_reminder` Task: a minimal auto-discovered Task.
- `host/server/main.py`: a small app factory that relies on YAML discovery.

Run its acceptance gate:

```bash
uv run python scripts/acceptance_check.py capability-template-playback \
  --report runs/acceptance/capability-template-playback.json
```

## Public extension API

Application code should import extension classes from the top-level package:

```python
from audio_chat import BaseTask, BaseTool, TaskEvent, ToolResult, UserDeviceContext
```

Tool and Task code must use `UserDeviceContext` for endpoint capabilities. Do not send
large media bytes in control event payloads; use `sensor.*` and `actuator.*` streams.
