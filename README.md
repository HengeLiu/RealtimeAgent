<p align="center">
  <img src="docs/assets/realtime-agent-brand.svg" alt="realtime-agent brand logo" width="420" />
  <br />
  <a href="docs/tutorials/developer-overview.md">开发者总览</a> ·
  <a href="agent-server/README.md">Server SDK</a> ·
  <a href="devices/README.md">Device SDK</a> ·
  <a href="protocol/README.md">协议</a> ·
  <a href="examples/README.md">示例</a> ·
  <a href="CONTRIBUTING.md">贡献指南</a>
</p>

大模型已经在 coding、chat 和专业领域展现出很强的能力，但如何稳定、自然地融入人类社会生活中的更多场景，仍然是一个困难且充满想象空间的问题。`realtime-agent` 希望降低拟人化对话和多端设备协作的开发门槛，为开发者提供一个可以快速试验、快速搭建应用的工具平台，让更多人把大模型接入真实设备、真实场景和真实生活。

`realtime-agent` 是一个面向实时语音、视觉输入和多设备协作的 Agent 开发框架。它把大模型对话、工具调用、后台任务、设备能力和运行排障组织成一套可扩展的 Server SDK、Device SDK 和通讯协议。如果你想做的不只是一个网页聊天机器人，而是一个可以听、说、看、调用设备、调度长流程任务的 AI 应用，这个项目可以作为基础框架。它适合智能眼镜、手机 App、浏览器摄像头 / 麦克风、嵌入式设备、机器人和其他需要稳定设备输入输出的实时多模态 Agent 应用，也适合 IoT / 智能家居场景：让大模型作为自然语言调度中心，协调智能开关、传感器、家电和其他终端硬件。

![realtime-agent 架构总览](docs/assets/realtime-agent-overview.svg)

## 可以构建什么

- **智能眼镜和可穿戴助手**：让设备通过语音和视觉理解用户眼前环境，完成问答、找物、导航提醒、信息播报和设备控制。
- **手机或浏览器里的实时多模态助手**：把麦克风、摄像头、屏幕提示和 speaker 输出接入同一个 Agent，用于视觉问答、远程协作、现场辅助或产品原型验证。
- **IoT 和智能家居自然语言调度**：把智能开关、传感器、家电、机器人或 Linux 网关接入 Device SDK，让用户通过对话触发硬件动作、查询设备状态或编排多设备联动。
- **面向业务系统的语音操作入口**：让用户用自然语言查询业务数据、触发工作流、调用内部 API，并在对话中获得实时反馈。
- **持续运行的环境观察和提醒应用**：把导航、巡检、看护、计时、状态监测等长流程做成后台任务，让 Agent 不只回答一次问题，也能持续跟进。
- **多设备协作的 AI 应用**：让眼镜、手机、浏览器、嵌入式设备或自定义硬件在同一个用户会话下协作输入、输出和消费事件。
- **可排查、可迭代的实时 Agent 产品**：通过运行产物复盘模型请求、工具调用、stream、输出和播放决策，把效果、延迟和稳定性问题定位到具体链路。

## 当前能力

当前项目已经围绕 Protocol、Server SDK、Device SDK 和开发支持工具打通了实时 Agent 应用的基础闭环。

**Protocol**

- 实时音视频对话：定义设备注册、音频上行、视觉输入、speaker 下行、stream chunk 和输出生命周期。
- 跨端事件消费：支持 server 向设备下发控制事件、自定义命令和输出事件，设备通过回执、进度和结果事件反馈执行状态。

**Server SDK**

- Omni + VL：已支持 Omni / Realtime 链路；VL 链路已具备 ASR、视觉模型、工具调用和 TTS 组合能力，后续还需要继续完善效果、延迟和稳定性。
- Tool + Task：支持把一次性业务动作建模为 `Tool`，把持续运行、维护状态或消费 stream 的流程建模为后台 `Task`。

**Device SDK**

- Swift + JS + ESP32：当前重点覆盖 Swift Device SDK、浏览器 / JavaScript 端侧 SDK，以及 ESP32 / 嵌入式设备接入方向。
- SDK + App：提供 Device SDK 和可运行的端侧 App / demo，开发者可以从示例验证注册、音视频链路和控制事件，再接入自己的硬件。
- 音视频采集 + 端侧回声抑制：支持端侧麦克风、相机和 speaker 链路接入，并在端侧音频会话中处理语音采集、播放和回声抑制边界。

**开发支持**

- 运行产物：记录模型请求、Agent 事件、工具事件、stream 事件、输出决策和播放决策，便于复盘一次真实对话。
- 测试工具：提供协议测试、SDK 测试、示例 App 契约测试，以及浏览器和 Python 端侧开发支持组件。
- 端侧语音唤醒：实现端侧基础的语音唤醒能力，使端侧开发测试更易入手。

## 后续计划

- 增强已有模块的运行稳定性。
- 优化 VL 链路的效果。
- 增加对大模型提示词开发的支持。
- 支持更多端侧设备。

## 快速开始

准备本地 Python 环境：

```bash
uv sync --python 3.11
uv pip install -e .
```

启动示例 Agent server：

```bash
uv run realtime-agent.server.run --config examples/device_demo/agent-server/server.yaml
```

默认服务地址：

```text
http://127.0.0.1:8765
```

检查服务状态：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

本地联调按这个顺序启动：

1. 启动 server：

   ```bash
   uv run realtime-agent.server.run --config examples/device_demo/agent-server/server.yaml
   ```
2. 打开浏览器眼镜模拟组件：

   ```bash
   uv run realtime-agent.web.open --serve
   ```

   浏览器组件会作为普通 Device 注册到 server，可用于测试麦克风输入、摄像头输入、server 下发的 speaker 输出、控制事件和 stream 生命周期。
3. 可选打开 Swift 真机 demo：

   ```bash
   uv run realtime-agent.ios.open
   ```

   真机运行时，在 iOS App 调试面板里把 server 地址改成 Mac 在同一局域网下可访问的地址，例如 `http://192.168.x.x:8765`。
4. 观察设备、播放和运行产物：

   ```bash
   curl http://127.0.0.1:8765/api/debug/devices
   curl http://127.0.0.1:8765/api/debug/playback
   find examples/device_demo/agent-server/runs -maxdepth 3 -type f | sort
   ```

如果只想快速打开浏览器模拟端，可以直接运行：

```bash
uv run realtime-agent.web.open --serve
```

运行一个最小契约测试：

```bash
uv run python -m pytest examples/device_demo/app-tests/test_ios_device_demo_contract.py -q
```

更多启动、扩展和排障说明见 [开发者总览](docs/tutorials/developer-overview.md)。

## 选择开发路径

### 构建 Agent 能力

如果你想让 Agent 执行新的业务动作，从这条路径开始。

大多数应用自己的能力放在：

```text
examples/<your-app>/agent-server/capabilities/
  tools.py
  tasks.py
```

一次性、短生命周期的动作适合写成 `Tool`。需要持续运行、维护状态、消费 stream 或多次输出的流程适合写成 `Task`。

常见修改包括：

- 在 `capabilities/tools.py` 中增加业务工具。
- 在 `capabilities/tasks.py` 中增加后台任务。
- 在应用配置中暴露新能力。
- 查看应用 `runs/` 目录里的运行产物。

建议从 [第一个 Tool 和 Task](docs/tutorials/build-first-capability.md) 开始。

### 接入设备

如果你想接入眼镜、手机 App、浏览器 UI、ESP32、机器人、Linux 网关或其他客户端，从这条路径开始。

设备侧代码负责：

- 注册到 server。
- 启用自己支持的 sensor、actuator、stream 和 command。
- 上传音频、图片、视频或传感器数据。
- 处理 server 下发的控制事件。
- 消费 speaker 输出或自定义设备命令。

当前 SDK 入口：

| SDK           | 入口                                            |
| ------------- | ----------------------------------------------- |
| Python        | [devices/python](devices/python/README.md)         |
| TypeScript    | [devices/typescript](devices/typescript/README.md) |
| Swift         | [devices/swift](devices/swift/README.md)           |
| Kotlin / Java | [devices/kotlin](devices/kotlin/README.md)         |
| C             | [devices/c](devices/c/README.md)                   |

设备接入模型见 [端侧 App 接入指南](devices/docs/device-app-integration.md)。

### 优化模型链路

如果你想提升回复质量、延迟、稳定性或 provider 行为，从这条路径开始。

`realtime-agent` 支持两类主要模型链路：

| 链路            | 适合场景                                                     | 代价                                        |
| --------------- | ------------------------------------------------------------ | ------------------------------------------- |
| Omni / Realtime | 更快跑通实时语音体验，组件更少                               | 对 ASR、视觉、LLM、TTS 等单独阶段的控制更少 |
| VL              | 更细控制 ASR、视觉模型、工具、上下文、提示词和 streaming TTS | 组件更多，延迟风险更高，调试成本更高        |

常见修改包括：

- 调整 system prompt、工具描述和任务描述。
- 调整上下文组装和视觉资产进入模型的方式。
- 替换 ASR、TTS、视觉模型或 realtime 模型 provider。
- 配置 OpenAI-compatible 或 DashScope-compatible 模型服务。
- 查看 `model-request.json`、`agent-events.jsonl` 以及 stream / playback 日志。

更完整的链路说明见 [开发者总览](docs/tutorials/developer-overview.md)。

## 核心概念

| 概念          | 含义                                                                                    |
| ------------- | --------------------------------------------------------------------------------------- |
| Server SDK    | Python 运行时，负责 session、agent loop、工具、任务、上下文、模型 provider 和运行产物。 |
| Device SDK    | 端侧 SDK，用于把真实设备或模拟设备接入 server 协议。                                    |
| Device        | 注册到 server 的客户端，声明自己的输入、输出、stream、command 或自定义硬件能力。        |
| Tool          | Agent 可以在对话中调用的短生命周期动作。                                                |
| Task          | Agent 可以启动、观测、发送信号和取消的长流程任务。                                      |
| Context API   | Tool 和 Task 用来请求设备能力、资产、输出和运行时数据的 SDK 接口。                      |
| Model Lane    | 模型执行链路，例如 Omni / Realtime 或 VL。                                              |
| Run Artifacts | 记录模型请求、工具事件、stream 事件、输出决策和播放决策的调试产物。                     |

## 仓库结构

```text
agent-server/   Python server SDK 和服务端运行时，见 agent-server/README.md
devices/        Python、TypeScript、Swift、Kotlin/Java、C 的 Device SDK，见 devices/README.md
protocol/       共享协议文档、fixture 和协议测试
examples/       示例应用、设备模拟器、回放测试和硬件参考工程
docs/           入门文档、参考文档、how-to 文档和设计说明
testdata/       共享测试资产，例如录制音频样例
tools/          开发和校验工具
```

项目边界可以按这个原则理解：

> 业务能力放应用目录，设备能力放端侧，通用框架能力才放 SDK 核心。

## 示例

主要示例应用是：

```text
examples/device_demo/
```

它是面向端侧 App 开发者的最小 Swift 真机 demo，用于验证 Device SDK 的设备注册、音频上行、相机帧上传、speaker 下行播放和控制事件。

开发支持设备包括：

- 浏览器眼镜模拟组件：`uv run realtime-agent.web.open --serve`
- Swift 真机 demo：`examples/device_demo/ios/`
- Python 手机视觉模拟组件：`examples/dev-support/devices/python-phone/`
- Python playback glass：`examples/dev-support/devices/python-playback-glass/`

当前示例清单见 [示例](examples/README.md)。

## 排查运行问题

示例应用的运行产物默认写到：

```text
examples/device_demo/agent-server/runs
```

最常用的文件：

| 文件                         | 用途                                           |
| ---------------------------- | ---------------------------------------------- |
| `model-request.json`       | 查看模型实际收到的消息、工具和上下文。         |
| `agent-events.jsonl`       | 查看服务端 Agent 和 provider 的关键事件。      |
| `tool-events.jsonl`        | 查看工具调用参数、结果、耗时和错误。           |
| `stream-events.jsonl`      | 查看音频、图片、视频和传感器 stream 生命周期。 |
| `output-decisions.jsonl`   | 查看服务端输出仲裁决策。                       |
| `playback-decisions.jsonl` | 查看端侧播放仲裁决策。                         |

这些产物是项目模型的一部分：实时 Agent 不应该只是能跑，还应该能在一次对话之后被排查和复盘。

## 文档

- [开发者总览](docs/tutorials/developer-overview.md)
- [第一个 Tool 和 Task](docs/tutorials/build-first-capability.md)
- [Server SDK](agent-server/README.md)
- [Device SDK](devices/README.md)
- [端侧 App 接入指南](devices/docs/device-app-integration.md)
- [设备事件行为标准](devices/docs/device-event-behavior.md)
- [CLI 参考](docs/internal/cli.md)
- [测试说明](docs/testing.md)
- [协议](protocol/README.md)

## 贡献

欢迎贡献。先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

也欢迎开发者尝试 AI Coding 范式来参与本项目。[AGENTS.md](AGENTS.md) 是给 AI 编程代理使用的仓库开发说明，记录了项目边界、协议规则、测试要求和文档约定。使用 AI 生成或修改代码后，请开发者重点审查代码质量、架构边界、协议兼容性、测试覆盖和文档一致性，不要把 AI 产出的代码直接视为已经完成审查。

本项目将持续完善自动测试和审查能力，提交变更前，运行和改动范围最相关的测试。对于 Device Demo 和 Swift Device SDK 入口改动，下面的契约测试可以作为一个轻量 smoke test：

```bash
uv run python -m pytest examples/device_demo/app-tests/test_ios_device_demo_contract.py -q
```

## License

本项目使用 [MIT License](LICENSE)。
