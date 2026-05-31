<p align="center">
  <img src="docs/assets/realtime-agent-brand.svg" alt="realtime-agent brand logo" width="420" />
  <br />
  <a href="docs/getting-started/developer-overview.md">Developer Overview</a> ·
  <a href="examples/README.md">Examples</a> ·
  <a href="devices">Device SDK</a> ·
  <a href="protocol/README.md">Protocol</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

Large language models have already shown strong capabilities in coding, chat, and specialized domains. Still, making them stable and natural enough to fit into more everyday social and real-world scenarios remains difficult, while also being highly imaginative. `realtime-agent` aims to lower the development barrier for human-like conversation and multi-device collaboration, giving developers a platform for fast experiments and fast application building so more people can connect large models to real devices, real scenarios, and real life.

`realtime-agent` is an Agent development framework for realtime voice, visual input, and multi-device collaboration. It organizes model conversations, tool calls, background tasks, device capabilities, and runtime debugging into an extensible Server SDK, Device SDK, and communication protocol. If you want to build more than a web chatbot, and instead need an AI application that can listen, speak, see, call devices, and schedule long-running workflows, this project can serve as a foundation. It is suitable for smart glasses, mobile apps, browser camera / microphone experiences, embedded devices, robots, and other realtime multimodal Agent applications that need stable device input and output.

![realtime-agent architecture overview](docs/assets/realtime-agent-overview.svg)

## What You Can Build

- **Smart glasses and wearable assistants**: Help devices understand the user's surroundings through voice and vision, then answer questions, find objects, provide navigation reminders, speak information, or control device features.
- **Realtime multimodal assistants in mobile apps or browsers**: Connect microphones, cameras, screen hints, and speaker output to one Agent for visual Q&A, remote collaboration, field assistance, or product prototyping.
- **Voice interfaces for business systems**: Let users query business data, trigger workflows, call internal APIs, and receive realtime feedback through natural language.
- **Long-running observation and reminder apps**: Model navigation, inspection, care, timers, status monitoring, and similar workflows as background tasks, so the Agent can keep following up instead of answering only once.
- **Multi-device AI applications**: Let glasses, phones, browsers, embedded devices, or custom hardware collaborate on input, output, and event consumption within the same user session.
- **Debuggable and iterative realtime Agent products**: Use run artifacts to inspect model requests, tool calls, streams, output decisions, and playback decisions, so quality, latency, and stability issues can be traced to the right link in the chain.

## Current Capabilities

The project has established a working foundation around Protocol, Server SDK, Device SDK, and developer support tools for realtime Agent applications.

**Protocol**

- Realtime audio and video conversation: defines device registration, audio upload, visual input, speaker output, stream chunks, and output lifecycle.
- Cross-device event consumption: supports server-to-device control events, custom commands, and output events, with device feedback through acknowledgement, progress, and result events.

**Server SDK**

- Omni + VL: supports the Omni / Realtime path today. The VL path already combines ASR, vision models, tool calls, and TTS, and still needs further work on quality, latency, and stability.
- Tool + Task: supports modeling one-shot business actions as `Tool`s, and long-running, stateful, or stream-consuming workflows as background `Task`s.

**Device SDK**

- Swift + JS + ESP32: currently focuses on the Swift Device SDK, browser / JavaScript device SDK, and ESP32 / embedded device integration.
- SDK + App: provides Device SDKs and runnable device-side apps / demos, so developers can validate registration, audio-video links, and control events before connecting their own hardware.
- Audio/video capture + endpoint echo cancellation: supports endpoint microphone, camera, and speaker chains, with audio session boundaries for voice capture, playback, and echo cancellation.

**Developer Support**

- Run artifacts: record model requests, Agent events, tool events, stream events, output decisions, and playback decisions for post-conversation debugging.
- Testing tools: provide protocol tests, SDK tests, example app contract tests, and browser / Python device-side development support components.
- Endpoint wake word: provides basic endpoint-side wake-word capability to make device-side development and testing easier to start.

## Roadmap

- Improve runtime stability across existing modules.
- Improve the quality of the VL path.
- Add better support for large-model prompt development.
- Support more endpoint devices.

## Quickstart

Prepare the local Python environment:

```bash
uv sync --python 3.11
uv pip install -e .
```

Start the example Agent server:

```bash
uv run realtime-agent.server.run --config examples/device_demo/agent-server/server.yaml
```

Default server address:

```text
http://127.0.0.1:8765
```

Check server status:

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

Open the browser glass simulator in another terminal:

```bash
uv run realtime-agent.web.open --serve
```

The browser component registers with the server as a normal Device. It can be used to test microphone input, camera input, server speaker output, control events, and stream lifecycle behavior.

Run a minimal contract test:

```bash
uv run python -m pytest examples/device_demo/app-tests/test_ios_device_demo_contract.py -q
```

For more startup, extension, and debugging guidance, see the [Developer Overview](docs/getting-started/developer-overview.md).

## Choose Your Path

### Build Agent Capabilities

If you want the Agent to perform new business actions, start here.

Most application-specific capabilities live under:

```text
examples/<your-app>/agent-server/capabilities/
  tools.py
  tasks.py
```

Use a `Tool` for a one-shot, short-lived action. Use a `Task` for workflows that run over time, maintain state, consume streams, or emit multiple outputs.

Common changes include:

- Add a business tool in `capabilities/tools.py`.
- Add a background task in `capabilities/tasks.py`.
- Expose the capability in the application config.
- Inspect run artifacts under the application's `runs/` directory.

Start with [Build Your First Tool and Task](docs/tutorials/build-first-capability.md).

### Connect a Device

If you want to connect glasses, a mobile app, browser UI, ESP32, robot, Linux gateway, or another client, start here.

Device-side code is responsible for:

- Registering with the server.
- Enabling its supported sensors, actuators, streams, and commands.
- Uploading audio, images, video, or sensor data.
- Handling control events sent by the server.
- Consuming speaker output or custom device commands.

Current SDK entry points:

| SDK | Entry |
| --- | --- |
| Python | [devices/python](devices/python/README.md) |
| TypeScript | [devices/typescript](devices/typescript/README.md) |
| Swift | [devices/swift](devices/swift/README.md) |
| Kotlin / Java | [devices/kotlin](devices/kotlin/README.md) |
| C | [devices/c](devices/c/README.md) |

For the device integration model, see [Device App Integration](docs/reference/device-app-integration.md).

### Tune the Model Path

If you want to improve response quality, latency, stability, or provider behavior, start here.

`realtime-agent` supports two primary model paths:

| Path | Best For | Tradeoff |
| --- | --- | --- |
| Omni / Realtime | Getting realtime voice experiences running faster with fewer components | Less control over individual ASR, vision, LLM, and TTS stages |
| VL | Finer control over ASR, vision models, tools, context, prompts, and streaming TTS | More components, higher latency risk, and higher debugging cost |

Common changes include:

- Adjust system prompts, tool descriptions, and task descriptions.
- Adjust context assembly and how visual assets enter the model.
- Replace ASR, TTS, vision model, or realtime model providers.
- Configure OpenAI-compatible or DashScope-compatible model services.
- Inspect `model-request.json`, `agent-events.jsonl`, and stream / playback logs.

For a fuller explanation of the model paths, see the [Developer Overview](docs/getting-started/developer-overview.md).

## Core Concepts

| Concept | Meaning |
| --- | --- |
| Server SDK | Python runtime for sessions, agent loops, tools, tasks, context, model providers, and run artifacts. |
| Device SDK | Endpoint SDKs for connecting real or simulated devices to the server protocol. |
| Device | A client registered with the server, declaring its input, output, stream, command, or custom hardware capabilities. |
| Tool | A short-lived action the Agent can call during a conversation. |
| Task | A long-running workflow the Agent can start, observe, signal, and cancel. |
| Context API | SDK interface used by Tools and Tasks to request device capabilities, assets, outputs, and runtime data. |
| Model Lane | A model execution path, such as Omni / Realtime or VL. |
| Run Artifacts | Debug artifacts that record model requests, tool events, stream events, output decisions, and playback decisions. |

## Repository Layout

```text
agent-server/   Python server SDK and server-side runtime
devices/        Device SDKs for Python, TypeScript, Swift, Kotlin/Java, and C
protocol/       Shared protocol docs, fixtures, and protocol tests
examples/       Example apps, device simulators, replay tests, and hardware references
docs/           Getting started docs, references, how-to docs, and design notes
testdata/       Shared test assets such as recorded audio samples
tools/          Development and validation tools
```

The project boundary can be summarized as:

> Business capabilities belong in app directories, device capabilities belong on the endpoint side, and reusable framework behavior belongs in the SDK core.

## Examples

The main example app is:

```text
examples/device_demo/
```

It is a minimal Swift hardware demo for device-side app developers, used to validate Device SDK registration, audio upload, camera frame upload, speaker output playback, and control events.

Developer support devices include:

- Browser glass simulator: `uv run realtime-agent.web.open --serve`
- Swift hardware demo: `examples/device_demo/ios/`
- Python phone visual simulator: `examples/dev-support/devices/python-phone/`
- Python playback glass: `examples/dev-support/devices/python-playback-glass/`

See [Examples](examples/README.md) for the current example inventory.

## Debugging Runs

Example app run artifacts are written to:

```text
examples/device_demo/agent-server/runs
```

The most useful files are:

| File | Purpose |
| --- | --- |
| `model-request.json` | Inspect the exact messages, tools, and context received by the model. |
| `agent-events.jsonl` | Inspect key server-side Agent and provider events. |
| `tool-events.jsonl` | Inspect tool call arguments, results, latency, and errors. |
| `stream-events.jsonl` | Inspect audio, image, video, and sensor stream lifecycle events. |
| `output-decisions.jsonl` | Inspect server-side output arbitration decisions. |
| `playback-decisions.jsonl` | Inspect endpoint playback arbitration decisions. |

These artifacts are part of the project model: a realtime Agent should not only run, but also be debuggable and reviewable after a conversation.

## Documentation

- [Developer Overview](docs/getting-started/developer-overview.md)
- [Project Layout](docs/reference/project-layout.md)
- [Device App Integration](docs/reference/device-app-integration.md)
- [CLI Reference](docs/reference/cli.md)
- [Testing](docs/testing.md)
- [Protocol](protocol/README.md)

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and the repository development notes in [AGENTS.md](AGENTS.md).

Before submitting changes, run the narrowest relevant tests for your change. For Device Demo and Swift Device SDK entrypoint changes, the following contract test is a lightweight smoke test:

```bash
uv run python -m pytest examples/device_demo/app-tests/test_ios_device_demo_contract.py -q
```

## License

Check the repository license before using this project in production or redistributing it.
