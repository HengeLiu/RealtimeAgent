# realtime-agent

<p align="center">
  <img src="docs/assets/realtime-agent-logo.svg" alt="realtime-agent logo" width="120" />
</p>

`realtime-agent` is a framework for building realtime AI agents that can listen, speak, see, call tools, run background tasks, and coordinate with device-side capabilities.

It is designed for applications beyond a web chat box: smart glasses, phones, browser camera and microphone apps, embedded devices, robots, and other realtime multimodal agents that need stable device I/O, model orchestration, and observable runs.

**Start here:** [Quickstart](docs/getting-started/quickstart.md) · [Developer Overview](docs/getting-started/developer-overview.md) · [Examples](examples/README.md) · [Device SDKs](devices) · [Protocol](protocol/README.md) · [Contributing](CONTRIBUTING.md)

![realtime-agent architecture overview](docs/assets/realtime-agent-overview.svg)

## What You Can Build

- Realtime voice agents that support interruption, low-latency playback, and output recovery.
- Vision-assisted agents that can use images, video frames, and device streams during a conversation.
- Device-aware assistants for smart glasses, mobile apps, browser devices, embedded hardware, or custom clients.
- Agents that call business tools, external APIs, device commands, and local services.
- Long-running background tasks for navigation, reminders, object finding, inspection, monitoring, and other stateful workflows.
- Observable agent applications where model requests, tool calls, streams, output decisions, and playback decisions can be inspected after a run.

## Quickstart

Prepare the local Python environment:

```bash
uv sync --python 3.11
uv pip install -e .
```

Start the example agent server:

```bash
uv run realtime-agent.server.run --config examples/device_demo/agent-server/server.yaml
```

The server runs at:

```text
http://127.0.0.1:8765
```

Check that it is healthy:

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

Open the browser glass simulator in another terminal:

```bash
uv run realtime-agent.web.open --serve
```

The browser component registers as a normal device and can be used to test microphone input, camera input, server speaker output, control events, and stream lifecycle behavior.

Run a minimal contract test:

```bash
uv run python -m pytest examples/device_demo/app-tests/test_ios_device_demo_contract.py -q
```

For the full first-run guide, see [Quickstart](docs/getting-started/quickstart.md).

## Choose Your Path

### Build Agent Capabilities

Use this path when you want the agent to perform new business actions.

Most application-specific capabilities live under:

```text
examples/<your-app>/agent-server/capabilities/
  tools.py
  tasks.py
```

Use a `Tool` for one short-lived action that should return quickly. Use a `Task` for a workflow that runs over time, maintains state, consumes streams, or emits multiple updates.

Typical changes:

- Add a business tool in `capabilities/tools.py`.
- Add a background task in `capabilities/tasks.py`.
- Expose the capability in the app config.
- Inspect run artifacts under the app `runs/` directory.

Start with [Build Your First Tool and Task](docs/tutorials/build-first-capability.md).

### Connect a Device

Use this path when you want to connect glasses, a phone app, browser UI, ESP32 board, robot, Linux gateway, or another client.

Device-side code is responsible for:

- Registering with the server.
- Enabling supported sensors, actuators, streams, and commands.
- Uploading audio, image, video, or sensor data.
- Handling server control events.
- Consuming speaker output or custom device commands.

Available SDK areas:

| SDK | Entry |
| --- | --- |
| Python | [devices/python](devices/python/README.md) |
| TypeScript | [devices/typescript](devices/typescript/README.md) |
| Swift | [devices/swift](devices/swift/README.md) |
| Kotlin / Java | [devices/kotlin](devices/kotlin/README.md) |
| C | [devices/c](devices/c/README.md) |

For the device integration model, see [Device App Integration](docs/reference/device-app-integration.md).

### Tune the Model Chain

Use this path when you want to improve response quality, latency, stability, or provider behavior.

`realtime-agent` supports two primary model-chain styles:

| Chain | Best for | Tradeoff |
| --- | --- | --- |
| Omni / Realtime | Fast realtime voice interaction with fewer moving parts | Less control over individual ASR, vision, LLM, and TTS stages |
| VL | More control over ASR, vision model, tools, context, prompts, and streaming TTS | More components, higher latency risk, more debugging work |

Typical changes:

- Adjust system prompts, tool descriptions, and task descriptions.
- Tune context assembly and visual asset usage.
- Replace ASR, TTS, vision, or realtime model providers.
- Configure OpenAI-compatible or DashScope-compatible model services.
- Inspect `model-request.json`, `agent-events.jsonl`, and stream or playback logs.

For a deeper model-chain overview, see [Developer Overview](docs/getting-started/developer-overview.md).

## Core Concepts

| Concept | Meaning |
| --- | --- |
| Server SDK | Python runtime for sessions, agent loops, tools, tasks, context, model providers, and run artifacts. |
| Device SDK | Client-side SDKs that connect real devices or simulators to the server protocol. |
| Device | A registered client with declared inputs, outputs, streams, commands, or custom hardware capabilities. |
| Tool | A short-lived action the agent can call during a conversation. |
| Task | A long-running workflow the agent can start, observe, signal, and cancel. |
| Context API | The SDK surface tools and tasks use to request device capabilities, assets, outputs, and runtime data. |
| Model Lane | A model execution path, such as Omni / Realtime or VL. |
| Run Artifacts | Debug files that record model requests, tool events, stream events, output decisions, and playback decisions. |

## Repository Layout

```text
agent-server/   Python server SDK and server-side runtime
devices/        Device SDKs for Python, TypeScript, Swift, Kotlin/Java, and C
protocol/       Shared protocol docs, fixtures, and protocol tests
examples/       Example apps, device simulators, replay tests, and hardware references
docs/           Getting-started guides, reference docs, how-to docs, and design notes
testdata/       Shared test assets such as recorded audio samples
tools/          Development and validation utilities
```

The main project boundary is:

> Business capabilities belong in app directories, device capabilities belong on the device side, and reusable framework behavior belongs in the SDK core.

## Examples

The main example app is:

```text
examples/device_demo/
```

It is the minimal Swift hardware demo for device-side app developers. It validates Device SDK registration, microphone upload, camera frame upload, speaker playback, and control events.

Development support devices include:

- Browser glass simulator: `uv run realtime-agent.web.open --serve`
- Swift hardware demo: `examples/device_demo/ios/`
- Python phone visual simulator: `examples/dev-support/devices/python-phone/`
- Python playback glass: `examples/dev-support/devices/python-playback-glass/`

See [Examples](examples/README.md) for the current example inventory.

## Debugging Runs

Example app runs are written under:

```text
examples/device_demo/agent-server/runs
```

The most useful files are:

| File | Use |
| --- | --- |
| `model-request.json` | Inspect the exact messages, tools, and context sent to the model. |
| `agent-events.jsonl` | Follow server-side agent and provider events. |
| `tool-events.jsonl` | Inspect tool arguments, results, timing, and errors. |
| `stream-events.jsonl` | Inspect audio, image, video, and sensor stream lifecycle events. |
| `output-decisions.jsonl` | Inspect server output arbitration decisions. |
| `playback-decisions.jsonl` | Inspect device playback arbitration decisions. |

These artifacts are part of the project model: a realtime agent should not only run, it should also be debuggable after a conversation.

## Documentation

- [What Is realtime-agent](docs/getting-started/what-is-realtime-agent.md)
- [Quickstart](docs/getting-started/quickstart.md)
- [Developer Overview](docs/getting-started/developer-overview.md)
- [Project Layout](docs/reference/project-layout.md)
- [Device App Integration](docs/reference/device-app-integration.md)
- [CLI Reference](docs/reference/cli.md)
- [Testing](docs/testing.md)
- [Protocol](protocol/README.md)

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the repository-specific development notes in [AGENTS.md](AGENTS.md).

Before sending changes, run the narrowest relevant test set for your change. For Device Demo and Swift Device SDK entrypoint changes, the contract test below is a useful smoke test:

```bash
uv run python -m pytest examples/device_demo/app-tests/test_ios_device_demo_contract.py -q
```

## License

See the repository license information before using this project in production or redistributing it.
