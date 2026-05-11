# 仓库开发指南

## 项目定位

`audio-chat` 是一个面向语音交互、多设备协作和实时数据流的服务端 Python SDK，加上可运行的示例应用和参考端侧实现。AI 编程代理进入本仓库时，默认应把它当作“SDK + 示例应用 + 多端协议”仓库处理，而不是单一业务脚本项目。

核心目标：

- 服务端负责设备注册、控制事件、数据流生命周期、Agent Core、工具 / 任务调度、输出播放仲裁和运行产物记录。
- 端侧负责录音、播放、相机、传感器、震动、视频显示、硬件驱动和控制信令处理。
- 业务能力通过应用目录下的工具 / 任务暴露给 Agent，不直接写进 SDK 核心包。
- `legacy/` 只作为旧实现和迁移参考，除非任务明确要求，不要从 `legacy/` 开始改主线功能。

## 主要目录

```text
audio-server/audio_chat/          # SDK 主体，Python 导入名 audio_chat
audio-server/tests/               # SDK 测试
audio-server/docs/                # SDK 内部设计、上下文 API、运行产物说明
docs/                             # 社区向文档、快速开始、教程、命令行和项目结构说明
examples/for-blind-app/           # 当前主要示例应用
examples/dev-support/             # 浏览器、Python 手机、Python 眼镜等本地参考端
testdata/                         # 可复用测试和回放样例
legacy/                           # 旧项目代码，仅迁移参考
```

SDK 关键模块：

```text
audio-server/audio_chat/
  agent_core/       # 文本 / 实时音频 Agent Core
  asset/            # 图片、音频等资产服务
  audio_pipeline/   # 音频输入输出链路
  cli/              # audio-chat.* 命令入口
  control/          # 设备注册、控制事件、事件路由
  output/           # 输出服务与播放仲裁
  stream/           # 数据流生命周期和字节传输
  spec/             # 随包 JSON schema
  context.py        # ToolContext / TaskContext 等上下文类型
  tools.py          # BaseTool / ToolResult 等工具基础类型
  tasks.py          # BaseTask 等任务基础类型
```

示例应用入口：

```text
examples/for-blind-app/audio-server/
  server.yaml
  capabilities/
    tools.py
    tasks.py
  runs/             # 本地运行产物，不能提交
```

参考端侧入口：

```text
examples/dev-support/devices/browser-glass/
examples/dev-support/devices/python-glass/
examples/dev-support/devices/python-phone/
examples/for-blind-app/devices/native-ios-phone/
examples/for-blind-app/devices/native-esp32-glass/
```

## 开发环境

使用 Python 3.11。`pyproject.toml` 限定 `>=3.11,<3.13`，本地优先用 `uv`：

```bash
uv sync --python 3.11
uv pip install -e .
```

如果 `uv run audio-chat.*` 找不到命令，先重新执行 editable 安装。不要默认使用系统 Python 跑测试；如果必须临时排障，先说明解释器版本和 `PYTHONPATH` 差异。

常用依赖和入口来自 `pyproject.toml`：

- 运行依赖：`aiohttp`、`dashscope`、`openai`、`opencv-python`、`pydantic`、`pyyaml`。
- 测试依赖：`pytest`。
- pytest 已配置 `pythonpath`，覆盖 `audio-server`、Python 参考端和 ESP32 参考包路径。

## 常用命令

启动主示例应用：

```bash
uv run audio-chat.server.run --app-name for-blind-app
```

按配置启动：

```bash
uv run audio-chat.server.run --config examples/for-blind-app/audio-server/server.yaml
```

健康检查和调试接口：

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/debug/devices
curl http://127.0.0.1:8765/api/debug/playback
```

校验设备能力文件：

```bash
uv run audio-chat.device.validate examples/dev-support/devices/browser-glass/device.audio-chat.yaml
uv run audio-chat.device.validate examples/dev-support/devices/browser-glass/device.audio-chat.yaml --json
```

打开浏览器参考端：

```bash
uv run audio-chat.web.open --print-url
```

Python 手机模拟端：

```bash
uv run python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

Python 手机 RGB 预览端：

```bash
uv run python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

Python 眼镜播放端：

```bash
uv run audio-chat.playback.glass --config examples/dev-support/devices/python-glass/playback.yaml
```

iOS 参考端：

```bash
uv run audio-chat.ios.open
uv run audio-chat.ios.build-sim
```

ESP32-S3 参考端：

```bash
uv run audio-chat.esp32.config
uv run audio-chat.esp32.build --dry-run
```

预检和发布包检查：

```bash
uv run audio-chat.dev.preflight --config examples/for-blind-app/audio-server/server.yaml
uv run audio-chat.sdk.package-check --report runs/default-app/package-check.json
```

## 架构边界

新代码必须优先遵守这些边界：

- SDK 核心包 `audio_chat` 提供通用能力，不放具体业务逻辑。
- 示例应用的业务工具 / 任务放在 `examples/<app>/audio-server/capabilities/`。
- 工具 / 任务只能通过 `ToolContext` / `TaskContext` 访问设备、资产、输出和上下文能力，不直接操作 WebSocket、内部服务对象或硬编码 `device_id`。
- 麦克风和扬声器属于系统音频主链路，不作为普通设备 `supports` capability 暴露。
- 图片、音频、视频、深度图等大字节数据必须走数据流或资产服务，不放进控制信令 JSON。
- 设备开发者只需要实现注册、能力声明、控制事件处理和数据流读写，不应该理解或依赖 Agent Core 内部实现。
- `legacy/` 中的旧路径、旧协议和旧配置名不能直接复制到主线代码；如果借鉴旧逻辑，先确认当前 `audio_chat` 的公开 API 和文档。

## 工具和任务开发规则

一次性、短生命周期动作写工具；持续运行、订阅数据流、维护状态或后台流程写任务。

公开导入优先使用：

```python
from audio_chat import BaseTask, BaseTool, TaskContext, ToolContext, ToolResult
```

工具常用能力：

- `context.devices.sensors.rgb.one()`：请求单帧 RGB 资产。
- `context.devices.actuators.vibrator.one()`：请求震动等执行器。
- `context.devices.commands.call()`：发送远程命令并等待端侧回报。
- `context.output.say()`：生成用户可听输出。
- `context.assets.get()`：读取资产。

任务在工具能力基础上可以使用持续数据流和命令订阅能力，例如 `.stream()`、`commands.start()`、`commands.subscribe_result()`。

新增能力建议顺序：

1. 判断能力应该实现为工具还是任务。
2. 写清楚需要哪些端侧能力，例如 `rgb`、`imu`、`tof`、`vibrator`。
3. 确认设备能力文件已经声明对应能力。
4. 通过上下文 API 请求设备能力，不绕过上下文。
5. 补充 pytest 或可复现联调流程。
6. 检查 `runs/` 产物，确认模型请求、工具调用、设备事件和输出链路都符合预期。

## 设备协议和能力声明

当前设备能力文件以结构化 `supports` 为准。示例：

```yaml
supports:
  sensors:
    - type: rgb
      modes: [single, continuous]
      default:
        format: jpeg
        frequency_hz: 1
        sample_count: 1
  actuators:
    - type: vibrator
      commands: [vibrate]
```

远程命令事件只使用：

- `command.requested`
- `command.accepted`
- `command.progress`
- `command.completed`
- `command.failed`

传感器数据流控制只使用：

- `stream.control.open.requested`
- `stream.control.close.requested`

不要新增临时协议名来绕过 schema。修改协议时必须同步更新 schema、文档、参考端和测试。

## 模型和模态

应用配置在：

```text
examples/for-blind-app/audio-server/server.yaml
```

文本模型链路：

```text
sensor.mic -> ASR -> TextAgentCore -> Tool -> Streaming TTS -> actuator.speaker
```

实时音频链路：

```text
sensor.mic -> RealtimeAudioAgentCore -> assistant_audio.delta -> actuator.speaker
```

真实 DashScope 模型提供方需要 `DASHSCOPE_API_KEY`。OpenAI 兼容文本模型需要 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。如果任务只是验证链路形状，可以使用模拟提供方；如果任务声称验证真实模型、真实 ASR、真实 TTS 或真实设备效果，必须说明实际使用的提供方、模型、端侧和日志证据。

## 测试策略

全部测试：

```bash
uv run python -m pytest
```

常用子集：

```bash
uv run python -m pytest audio-server/tests -q
uv run python -m pytest examples/for-blind-app/tests -q
uv run python -m pytest examples/dev-support/tests -q
uv run python -m pytest examples/for-blind-app/tests/test_text_route_audio_samples.py -q
```

真实模型提供方集成测试：

```bash
uv run python -m pytest audio-server/tests/integration/test_dashscope_providers.py -q
```

测试编写要求：

- 测试文件命名为 `test_*.py`。
- SDK 行为测试放 `audio-server/tests/`。
- 示例应用测试放 `examples/<app>/tests/`。
- 参考设备测试放对应 `examples/dev-support/tests/` 或示例应用测试目录。
- 新测试要用中文注释或 docstring 写明测试目标、测试方法和预期结果。
- 测试的目的是真实暴露问题，不是只为通过而放宽断言。
- 如果功能涉及跨设备，必须提供本地可复现联调流程和观察点。

## 跨设备联调流程

基础本地联调顺序：

1. 校验设备能力文件：

   ```bash
   uv run audio-chat.device.validate examples/dev-support/devices/browser-glass/device.audio-chat.yaml
   ```

2. 启动 server：

   ```bash
   uv run audio-chat.server.run --app-name for-blind-app
   ```

3. 打开浏览器参考端：

   ```bash
   uv run audio-chat.web.open --print-url
   ```

4. 可选启动 Python 手机模拟端：

   ```bash
   uv run python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
   ```

5. 可选启动 Python 眼镜播放端：

   ```bash
   uv run audio-chat.playback.glass --config examples/dev-support/devices/python-glass/playback.yaml
   ```

6. 检查：

   ```bash
   curl http://127.0.0.1:8765/api/health
   curl http://127.0.0.1:8765/api/debug/devices
   curl http://127.0.0.1:8765/api/debug/playback
   ```

iOS 和 ESP32 相关改动要额外说明实际验证层级：只是构建、模拟器、真机，还是硬件串口监视。不要把“契约测试通过”写成“真机已验证”。

## 运行产物和排障

`runs/` 是主要排障证据目录，默认位于应用目录下，例如：

```text
examples/for-blind-app/audio-server/runs
```

程序启动时，`audio_chat.runs` 会打印一次 `运行产物目录索引`，其中的 `runs_root` 就是当前应用的运行产物根目录。后续事件日志不再重复打印 `detail_path`、`session_detail_path` 或 `path`，避免终端被同一类存储路径刷屏；排查时按启动索引和下表定位文件。

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

一次 session 优先看：

```text
<user_id>/<device_id>/events.jsonl         # 设备注册、唤醒、音频 session、控制事件
<user_id>/<device_id>/messages.jsonl       # 用户、助手和工具消息历史
<user_id>/<device_id>/model-request.json   # 最近一次发给模型的请求快照
<user_id>/<device_id>/agent-events.jsonl   # Agent Core、模型 provider 和 delta 摘要事件
<user_id>/<device_id>/model-events.jsonl   # 模型相关事件镜像，便于按模型视角排查
<user_id>/<device_id>/tool-events.jsonl    # 工具调用参数、结果、耗时和错误
<user_id>/<device_id>/stream-events.jsonl  # 数据流生命周期和分片摘要
<user_id>/<device_id>/assets.jsonl         # 图片等资产写入和请求记录
<user_id>/<device_id>/task-signals.jsonl   # Task Engine 信号记录
<user_id>/<device_id>/output-decisions.jsonl    # 服务端输出仲裁决策
<user_id>/<device_id>/playback-decisions.jsonl  # 端侧播放仲裁决策
<user_id>/<device_id>/actuators.jsonl      # 端侧执行器播放和回执记录
<user_id>/<device_id>/audio/               # 麦克风输入 PCM 和扬声器输出 WAV
<user_id>/<device_id>/photos/              # RGB 图片或抓拍资产
<user_id>/<device_id>/imu/                 # IMU 数据
<user_id>/<device_id>/depth/               # 深度或 ToF 数据
<user_id>/<device_id>/assets/              # 其他资产
<user_id>/memory.json                      # 用户长期记忆
```

排查顺序：

1. 模型没反应：先看 `events.jsonl`、`stream-events.jsonl`、`agent-events.jsonl`、根目录 `system-events.jsonl`。
2. 模型没拿到工具或上下文：看 `model-request.json`。
3. 工具行为不符合预期：看 `tool-events.jsonl` 和相关资产文件。
4. 播放、打断、输出异常：看 `output-decisions.jsonl`、`playback-decisions.jsonl`、`/api/debug/playback`。
5. 设备没有收到事件：看根目录 `control-routes.jsonl`。

`runs/`、日志、真实用户音频、图片和视频不能提交。

## 日志和配置

- 协助排查的日志使用 `DEBUG`。
- 用户可见或关键状态使用 `INFO`。
- 降级、超时、协议不一致使用 `WARNING` 或 `ERROR`。
- 本地开发优先支持在配置中打开 DEBUG，不要把临时 `print()` 留在主线代码。
- 如果新增配置项，必须补默认值、示例配置、文档说明和测试。
- 不要提交 API Key、设备 token、Wi-Fi 密码、`.env`、本地 `AppConfig.json` 或硬件私有配置。

## 文档规则

写文档是为了记录重要决策、协议和联调方法，不是堆文字。涉及复杂架构、流程、时序时，优先使用 PlantUML。

文档入口：

- `README.md`：项目快速开始和主流程。
- `docs/reference/project-layout.md`：目录结构。
- `docs/reference/cli.md`：命令行参考。
- `audio-server/docs/how-to/device-capability-development.md`：设备能力与上下文 API。
- `audio-server/docs/how-to/inspect-runs-artifacts.md`：运行产物说明。
- `audio-server/docs/reference/context-api.md`：上下文 API 目标设计。
- `examples/for-blind-app/docs/`：示例应用相关设计和验收记录。

修改协议、目录结构、命令行、配置、运行产物或跨设备流程时，要同步更新相关文档。文档中的测试结果必须和真实命令结果一致，不能只写设计预期。

## 代码风格

- 使用 Python 3.11+。
- 遵守现有包边界，不新增全局硬编码路径。
- 公共 SDK API 尽量保持类型清晰，避免让示例应用依赖内部实现细节。
- 类、函数、测试新增注释和 docstring 使用中文，说明功能、主要逻辑、参数、返回值和异常情况。
- 临时诊断脚本或一次性排障代码要轻量，任务结束后删除，避免混入架构代码。
- 复杂或不确定实现先查文档、社区或成熟方案；简单能力可以在依赖成本和自研复杂度之间做平衡。
- 不要为了测试通过而牺牲真实功能语义。

## Git 和提交

- 提交信息使用简短中文，例如 `补齐设备能力文档`。
- 不允许直接 push 任意分支到远程，除非用户明确要求。
- 提交保持聚焦，不把无关格式化、运行产物和本地配置混进同一提交。
- 新工具如果产生缓存、构建产物、日志或媒体文件，必须同步更新 `.gitignore`。
- 当前 `.gitignore` 已覆盖 `.venv`、`runs`、Python 缓存、构建产物、ESP-IDF 和 iOS 常见产物；新增端侧工程时继续补齐对应构建目录。

## AI 代理工作准则

开始改代码前先确认任务属于哪一层：

- SDK 核心能力：改 `audio-server/audio_chat/`，补 `audio-server/tests/`。
- 示例应用能力：改 `examples/for-blind-app/audio-server/capabilities/`，补 `examples/for-blind-app/tests/`。
- 参考设备：改 `examples/dev-support/devices/` 或 `examples/for-blind-app/devices/`，补端侧契约或联调说明。
- 文档或协议：同步更新 docs、schema、测试和示例配置。

遇到多设备、模型、ASR、TTS、数据流、播放仲裁、工具调用问题时，不要只凭命名推断实现状态；要用代码位置、测试命令、运行产物和日志说明真实链路。

完成后应说明：

- 改了哪些文件。
- 影响 SDK、示例应用、参考端还是文档。
- 跑了哪些测试或检查。
- 如果没有跑某些关键测试，说明原因和建议的补充验证。
