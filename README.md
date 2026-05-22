# realtime-agent

`realtime-agent` 是一个面向大模型应用的实时音视频对话和跨设备任务管理框架。它把语音输入、视觉采集、模型对话、工具调用、后台任务、设备控制和播放仲裁组织成一套可扩展的 server-side Python SDK，让应用可以用同一个协议连接眼镜、手机、浏览器模拟器和嵌入式设备。

项目重点解决这些问题：

- 实时多模态对话：支持 Vision/VL 链路和 Omni Realtime 链路，能够处理语音、图片、视频帧和模型流式输出。
- 跨设备协作：设备通过统一协议注册能力，server 按 `user_id`、`device_id` 和 `supports` 路由传感器、执行器和控制事件。
- Tool / Task 运行时：短动作由 Tool 执行，持续监测、找物、看红绿灯、跨设备视觉任务等长流程由 Task 管理。
- 资产与上下文管理：图片等大字节数据进入统一资产链路，再按模型类型 append 到上下文，避免业务工具重复处理 provider 差异。
- 可观测和可回放：运行产物记录模型请求、设备事件、工具结果、任务信号、音频和图片，方便复盘真实链路。

server 不负责录音、播放、唤醒词、端侧 AEC 或硬件驱动；这些能力由端侧设备实现并通过协议声明。业务能力通过 Context 表达设备使用意图，不直接操作 WebSocket 或内部服务对象。当前可用开发方式以 [设备注册与功能开发说明](agent-server/docs/how-to/device-capability-development.md) 为准；完整 Context API 目标设计见 [Context 与设备 API 设计说明](agent-server/docs/reference/context-api.md)。

仓库主要目录：

- `agent-server/realtime_agent/`：Python server SDK 源码，发布包名为 `realtime-agent`，导入名为 `realtime_agent`。
- `devices/`：多语言端侧通讯 SDK，覆盖 Python、TypeScript、Swift、Kotlin/Java 和 C。
- `examples/`：示例应用、真实端侧参考工程和开发/测试支持组件。
- `protocol/`：server 和 device 共同依赖的协议文档、fixture 和协议资产检查。
- `docs/`：架构设计、联调和排障文档。

## 快速开始

准备环境：

```bash
uv sync --python 3.11
uv pip install -e .
```

如果 `uv run realtime-agent.*` 找不到命令，重新执行 editable 安装。

启动统一示例应用 server：

```bash
uv run realtime-agent.server.run --app-name for-blind-app
```

常用调试接口：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

如果需要后台管理 server，可使用：

```bash
uv run realtime-agent.server.start --config examples/for-blind-app/agent-server/server.yaml
uv run realtime-agent.server.logs
uv run realtime-agent.server.stop
```

## 启动开发支持组件

最新推荐联调顺序：

```bash
# 1. 校验端侧能力文件，确认设备能力声明有效
uv run realtime-agent.device.validate examples/dev-support/devices/browser-glass/device.realtime-agent.yaml

# 2. 启动应用 server
uv run realtime-agent.server.run --app-name for-blind-app

# 3. 打开浏览器眼镜模拟组件，连接并注册后开始语音或视觉测试
uv run realtime-agent.web.open --serve

# 4. 可选：启动 Python 手机视频/视觉模拟组件，验证同一 user_id 下多端协作
uv run --extra gui python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

这些组件在代码和协议上会注册成普通 Device，所以可以真实覆盖注册、控制事件、
stream、speaker 输出和 peer video。但它们属于 RealtimeAgent SDK 的开发/测试支持组件；
开发者的正式眼镜、手机、嵌入式设备可以在自己的工程里实现，只需要遵守同一协议。

浏览器眼镜模拟组件：

```bash
uv run realtime-agent.web.open --serve
```

Python 手机视频/视觉模拟组件：

```bash
uv run --extra gui python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

Python 手机简单 mock 组件：

```bash
uv run python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

`phone.preview.yaml` 会打开 PySide6 视频窗口，注册到 server，并订阅同一 `user_id` 下的
`sensor.rgb` 输入流。普通 realtime 视觉采样中的单资产帧带有 `request_id`，
只进入模型/资产链路，不会在 Task 前转发给 phone；只有普通连续 RGB stream 或
`peer.video.sender.start` 建立的视频任务流会显示在窗口里。最近一帧写入
`runs/realtime-agent/python-phone/latest-rgb.png`，YOLO 标注帧写入
`runs/realtime-agent/python-phone/latest-yolo.jpg`。

Python glass playback：

```bash
uv run realtime-agent.playback.glass --config examples/dev-support/devices/python-glass/playback.yaml
```

发布候选版本当前为 `0.1.0rc1`。发布前优先使用 `realtime-agent.sdk.package-check`、
`realtime-agent.dev.preflight` 和关键 pytest 子集确认包边界、示例应用和设备入口仍然一致。

使用录制音频驱动 playback：

```bash
uv run realtime-agent.playback.glass \
  --server-url http://127.0.0.1:8765 \
  --audio-wav testdata/audio-sample/看一下我前面有什么.wav
```

Vision 路线的无头验收可以直接复用 `testdata/audio-sample/` 下的 AudioSample。mock ASR 会把 WAV 文件名作为转写文本，mock vision model 会按文本意图触发真实 ToolGateway，因此这条链路能覆盖 `sensor.mic -> ASR -> VisionRealtimeAgentCore -> Tool -> Streaming TTS -> actuator.speaker`：

```bash
uv run python -m pytest examples/for-blind-app/replay-tests/test_vision_route_audio_samples.py -q
```

## 更换模型和模态

应用运行配置在 `examples/for-blind-app/agent-server/server.yaml`。`agent-server/config/server.example.yaml` 是带完整中文注释的模板，可以用来对照每个字段的作用；真正启动时仍然加载 app 根目录下名为 `server.yaml` 的文件。

如果要从 Omni Realtime 切到 Vision 模态测试，把 `agent.mode` 改成 `vision`，然后配置 `agent.vision` 三段 provider：

```yaml
agent:
  mode: "vision"
  vision:
    provider: "dashscope-compatible"
    model: "qwen-plus"
    asr_provider: "dashscope"
    asr_model: "fun-asr-realtime"
    tts_provider: "dashscope"
    tts_model: "cosyvoice-v3-flash"
    tts_voice: "longanhuan"
    streaming_tts: true
    allow_mock_fallback: true
```

这条链路是 `sensor.mic -> ASR -> VisionRealtimeAgentCore -> Tool -> Streaming TTS -> actuator.speaker`。当前 `asr_provider: "dashscope"` 走 `dashscope.audio.asr.Recognition` 实时接口，ASR 模型应使用 `fun-asr-realtime`；`qwen3-asr-flash` 是非实时录音文件识别/HTTP 调用模型，不适配这条实时麦克风路径。使用 DashScope 真实 provider 前需要设置：

```bash
export DASHSCOPE_API_KEY="你的 DashScope Key"
uv run realtime-agent.dev.preflight --config examples/for-blind-app/agent-server/server.yaml
uv run realtime-agent.server.run --app-name for-blind-app
```

如果只是先验证 Vision 链路形状，可以把 `provider/asr_provider/tts_provider` 都设成 `mock`。mock ASR 在 playback 测试里会用 WAV 文件名作为转写文本，mock TTS 会生成诊断音，不需要任何 API Key。

如果要接 OpenAI-compatible 或本地模型服务，只替换 Vision 模型段：

```yaml
agent:
  mode: "vision"
  vision:
    provider: "openai-compatible"
    model: "你的模型名"
```

并设置：

```bash
export OPENAI_API_KEY="你的 Key"
export OPENAI_BASE_URL="https://你的模型服务/v1"
```

ASR 和 TTS 目前可运行的真实实现主要是 DashScope；本地模型服务只替换 Vision 模型时，建议先保留 `asr_provider: "mock"`、`tts_provider: "mock"` 或继续使用 DashScope。

如果要切回 Omni Realtime，把 `agent.mode` 改成 `omni`，并配置 `agent.omni`：

```yaml
agent:
  mode: "omni"
  realtime:
    provider: "qwen"
    model: "qwen3.5-omni-plus-realtime"
    voice: "Tina"
    turn_detection: "provider"
    max_concurrent_sessions: 10
```

Omni Realtime 同样使用 `DASHSCOPE_API_KEY`。SDK 默认最多同时建立 10 条同一 provider / model / endpoint 的 Realtime 连接，达到上限时会拒绝新会话，避免继续冲击供应商限流。它的主链路是 `sensor.mic -> OmniRealtimeAgentCore -> assistant_audio.delta -> actuator.speaker`，不经过 VisionRealtimeAgentCore 的 ASR 和 TTS。

iOS 参考端：

```bash
uv run realtime-agent.ios.open
```

ESP32-S3 参考端：

```bash
uv run realtime-agent.esp32.config
uv run realtime-agent.esp32.build --dry-run
```

## 开发者工作模型

当前可执行的设备注册、Tool / Task 开发、调试和验收入口统一整理在 [设备注册与功能开发说明](agent-server/docs/how-to/device-capability-development.md)。完整 Context API、selector 规则、AssetRef 边界和设备能力结构整理在 [Context 与设备 API 设计说明](agent-server/docs/reference/context-api.md)。

当前开发口径：

- Tool 使用 `ToolContext`，只做短生命周期动作。
- Task 使用 `TaskContext`，可以做持续数据流和异步状态维护。
- Tool 使用已落地的 typed facade，例如 `context.devices.sensors.rgb.one()`、`context.devices.commands.call()`、`context.output.say()` 和 `context.assets.get()`。
- Task 在 Tool 能力基础上额外开放持续 stream 和长命令接口，例如 `context.devices.sensors.rgb.stream()`、`context.devices.commands.start()` 和 `context.devices.commands.subscribe_result()`。
- 麦克风和喇叭是系统音频通道，不作为普通设备能力开放。
- 设备能力文件当前只接受结构化 `supports.sensors[].type` 和 `supports.actuators[].type`；注册 payload 不允许手写 `routes`。
- `selector` 已可用于 typed sensor、actuator 和 command API 的设备筛选。

默认应用目录结构如下：

```text
examples/<your-app>/agent-server/
  server.yaml
  capabilities/
    __init__.py
    tools.py       # 继承 BaseTool，会被自动发现
    tasks.py       # 继承 BaseTask，会被自动发现
  skills/
  config/
```

常用公开基类可从 `realtime_agent` 顶层导入：

```python
from realtime_agent import BaseTask, BaseTool, ToolContext, ToolResult
```

`server.yaml` 中配置自动发现包后，开发者只需要把 `BaseTool` / `BaseTask` 子类放进对应 package。SDK 启动时会扫描这些类，把 Tool schema 注册给 Agent Core，把 Task 类型注册给 Task Engine。业务代码不需要在 `app.py` 里手写注册逻辑。

新增一个能力时，推荐按这个顺序设计：

1. 先判断能力是一次性动作还是长流程：一次性动作写 Tool，长流程写 Task。
2. 列出它需要哪些端侧能力：设备文件用 `supports.sensors` / `supports.actuators` 声明；业务代码使用 `context.devices.sensors.rgb`、`context.devices.sensors.imu`、`context.devices.sensors.tof`、`context.devices.actuators.vibrator` 这类 typed facade。
3. 确认端侧设备能力文件中已经声明对应能力，例如 `type: rgb`、`type: imu`、`type: tof` 或 `type: vibrator`。
4. 在 Tool / Task 中通过 Context API 表达能力调用，不直接操作 WebSocket 或硬编码 `device_id`。
5. 用 `<runs_root>/...` 中的运行产物验证链路。for-blind-app 默认写入 `examples/for-blind-app/agent-server/runs/`。

业务样例：

- `examples/for-blind-app`：盲人眼镜业务样例，包含找物、红绿灯、导航、搜索和计时器。
- `examples/for-blind-app/agent-server/capabilities`：业务能力样例。

关键约束：

- Tool / Task 只能通过 Context 公开 API 访问设备通讯能力。
- Tool 不持有 `tasks`、`memory`、`skills`、`mcp` 服务入口；这些能力通过独立 Tool 暴露给模型。
- 不直接把图片、音频、视频或文件字节放进控制信令，大字节数据必须走 stream。
- 设备开发者只实现注册能力、控制信令处理和 stream 读写，不需要理解 Agent Core 或业务 Tool。

## 设备开发

端侧开发者优先维护设备能力文件。浏览器示例：

```bash
uv run realtime-agent.device.validate examples/dev-support/devices/browser-glass/device.realtime-agent.yaml --json
```

设备能力文件当前用于声明结构化 `supports.sensors`、`supports.actuators`、运行环境和调试属性。`selector` 是 Tool / Task 调用设备时的运行期筛选条件，不写进设备能力文件；`external` 可用于端侧私有调试元数据。

端侧实现入口：

- `examples/dev-support/devices/browser-glass`
- `examples/dev-support/devices/python-glass`
- `examples/dev-support/devices/python-playback-glass`
- `examples/dev-support/devices/python-phone`
- `examples/for-blind-app/devices/native-ios-phone`
- `examples/for-blind-app/devices/native-esp32-glass`

iOS / ESP32 目录目前是参考端和契约入口，不代表真实 iOS 模型或 ESP32 真机效果已经完成。

## 配置同步与检查

生成本地联调配置：

```bash
uv run realtime-agent.config.sync --app-root examples/for-blind-app/agent-server
```

如果要让 iOS、ESP32 或其他局域网设备连接到这台 Mac，把 `server_url` 同步成
Mac 当前局域网 IP，而不是 `127.0.0.1`：

```bash
uv run realtime-agent.config.sync \
  --app-root examples/for-blind-app/agent-server \
  --server-url "http://$(ipconfig getifaddr en0):8765"
```

如果 Mac 当前使用的是有线网卡或其他网络接口，先用 `ifconfig` 确认实际 IP，
再把 `--server-url` 改成对应地址，例如 `http://192.168.1.23:8765`。

预检：

```bash
uv run realtime-agent.dev.preflight --config examples/for-blind-app/agent-server/server.yaml
```

发布包检查：

```bash
uv run realtime-agent.sdk.package-check --report runs/default-app/package-check.json
```

建议验收：

```bash
uv run realtime-agent.dev.preflight \
  --config examples/for-blind-app/agent-server/server.yaml \
  --report runs/acceptance/preflight.json

uv run realtime-agent.sdk.package-check \
  --report runs/acceptance/package-check.json

uv run python -m pytest \
  protocol/protocol-tests \
  agent-server/unit-tests \
  agent-server/protocol-tests \
  devices/python/unit-tests \
  devices/python/protocol-tests \
  examples/for-blind-app/app-tests \
  examples/for-blind-app/replay-tests \
  examples/dev-support/unit-tests \
  examples/dev-support/app-tests \
  -q
```

## 运行产物与日志索引

服务启动时，`realtime_agent.runs` 会打印一次 `运行产物目录索引`。其中 `runs_root` 是当前应用的运行产物根目录，排查时按启动索引定位文件。

根目录文件：

```text
control-events.jsonl      # 全局控制事件流水
control-routes.jsonl      # 控制事件订阅匹配和投递结果
system-events.jsonl       # 系统错误、降级和恢复事件
capability-events.jsonl   # 跨会话能力调用轨迹
command-events.jsonl      # 跨会话设备命令轨迹
debug/playback.json       # 当前播放仲裁快照，对应 /api/debug/playback
tasks/                    # 长流程 Task 运行产物
```

设备会话目录位于 `<runs_root>/<user_id>/<device_id>/`：

```text
events.jsonl              # 当前设备会话控制事件
messages.jsonl            # 用户、助手和工具消息历史
model-request.json        # 最近一次发给模型的请求快照
agent-events.jsonl        # Agent Core、模型 provider 和 delta 摘要事件
model-events.jsonl        # 模型相关事件镜像，便于按模型视角排查
tool-events.jsonl         # 工具调用参数、结果、耗时和错误
stream-events.jsonl       # 数据流打开、关闭、失败和分片摘要
assets.jsonl              # 图片等资产写入和请求记录
task-signals.jsonl        # Task Engine 信号记录
output-decisions.jsonl    # 服务端输出仲裁决策
playback-decisions.jsonl  # 端侧播放仲裁决策
actuators.jsonl           # 端侧执行器播放和回执记录
audio/                    # 麦克风输入 PCM 和扬声器输出 WAV
photos/                   # RGB 图片或抓拍资产
imu/                      # IMU 数据
depth/                    # 深度或 ToF 数据
assets/                   # 其他资产
```

用户级文件位于 `<runs_root>/<user_id>/`，其中 `memory.json` 保存用户长期记忆。

## 测试

项目测试按 P0 协议资产检查、L1 事件行为一致性、L2 大模型能力、L3 应用能力组织。完整说明见 [测试体系说明](docs/testing.md)。

```bash
uv run python -m pytest
```

常用分层回归：

```bash
uv run python -m pytest -m protocol -q
uv run python -m pytest -m sdk -q
uv run python -m pytest -m device_sdk -q
uv run python -m pytest -m model_provider -q
uv run python -m pytest -m replay -q
```

真实 provider 集成测试需要配置对应 API Key：

```bash
uv run python -m pytest agent-server/model-provider-tests/test_dashscope_providers.py -q
```

## 文档

- [文档目录](docs/README.md)
- [设备注册与功能开发说明](agent-server/docs/how-to/device-capability-development.md)
- [Context 与设备 API 设计说明](agent-server/docs/reference/context-api.md)
- [运行产物说明](agent-server/docs/how-to/inspect-runs-artifacts.md)
- [内部设计文档](agent-server/docs/README.md)
