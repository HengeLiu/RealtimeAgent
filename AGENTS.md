# 仓库开发指南

## 项目定位

`realtime-agent` 是面向语音交互、多设备协作和实时数据流的 Server SDK + 多语言 Device SDK + 示例和开发支持组件仓库。AI 编程代理进入本仓库时，默认按“SDK、协议、端侧参考、开发支持组件、文档体系”共同维护，而不是单一业务脚本项目。

核心边界：

- Server SDK 负责设备注册、控制事件、数据流生命周期、Agent Core、工具 / 任务调度、输出播放仲裁和运行产物记录。
- Device SDK / 端侧负责录音、播放、相机、传感器、震动、视频显示、硬件驱动和控制信令处理。
- 业务能力通过应用目录下的 Tool / Task 暴露给 Agent，不写进 SDK 核心包。
- `dev-support/` 是开发和测试支持组件，不是正式产品形态。
- `legacy/` 只作为迁移参考，除非任务明确要求，不要从 `legacy/` 开始改主线功能。

## 权威文档入口

重复信息以这些文档为准，`AGENTS.md` 只保留开发代理需要立刻遵守的规则：

- [README.md](README.md)：项目入口、快速开始、本地多设备启动顺序。
- [docs/README.md](docs/README.md)：社区向文档导航。
- [docs/tutorials/developer-overview.md](docs/tutorials/developer-overview.md)：开发者总体导览。
- [docs/tutorials/build-first-capability.md](docs/tutorials/build-first-capability.md)：第一个 Tool / Task。
- [docs/internal/cli.md](docs/internal/cli.md)：CLI 命令参考。
- [docs/testing.md](docs/testing.md)：测试分层、测试命令和验收边界。
- [agent-server/README.md](agent-server/README.md)：Server SDK 目录和职责。
- [devices/README.md](devices/README.md)：多语言 Device SDK 目录和协议入口。
- [agent-server/docs/README.md](agent-server/docs/README.md)：Server SDK 内部设计文档索引。
- [agent-server/docs/reference/上下文设备接口设计.md](agent-server/docs/reference/上下文设备接口设计.md)：Context 与设备 API 目标设计。
- [agent-server/docs/how-to/运行产物排查说明.md](agent-server/docs/how-to/运行产物排查说明.md)：runs 产物和排障入口。
- [examples/device_app_demo/README.md](examples/device_app_demo/README.md)：Swift Device SDK 真机 demo。

修改目录结构、命令行、协议、运行产物或跨设备流程时，必须同步更新上面的对应文档。文档中的命令、测试结果和真实实现必须一致。

## 主要目录

```text
agent-server/realtime_agent/      # Server SDK 主包，Python 导入名 realtime_agent
agent-server/unit-tests/          # Server SDK 单元测试和 CLI 边界测试
agent-server/protocol-tests/      # Server SDK 协议行为和系统级契约测试
agent-server/model-provider-tests/# 真实模型 provider 集成测试
agent-server/docs/                # Server SDK 内部设计、Context API、运行产物说明
devices/                          # 多语言 Device SDK
protocol/                         # 协议说明、协议数据资产和协议资产检查
docs/                             # 社区向文档、教程、CLI、测试说明
examples/device_app_demo/             # Swift Device SDK 真机验证示例
dev-support/             # browser-glass、Python phone、Python playback 等开发支持组件
testdata/                         # 可复用测试和回放样例
legacy/                           # 旧实现和迁移参考
```

## 开发环境

- 使用 Python 3.11；`pyproject.toml` 限定 `>=3.11,<3.13`。
- 本地优先使用 `uv sync --python 3.11` 和 `uv pip install -e .`。
- 不要默认使用系统 Python 跑测试；如果临时排障必须使用，说明解释器版本和 `PYTHONPATH` 差异。
- 如果 `uv run realtime-agent.*` 找不到命令，先重新执行 editable 安装。

## 架构边界

- SDK 核心包 `realtime_agent` 提供通用能力，不放具体业务逻辑。
- 应用业务 Tool / Task 放在 `examples/<app>/agent-server/capabilities/` 或外部应用自己的 app-root。
- Tool / Task 只能通过 `ToolContext` / `TaskContext` 访问设备、资产、输出和上下文能力，不直接操作 WebSocket、内部服务对象或硬编码 `device_id`。
- 麦克风和扬声器属于系统音频主链路，不作为普通设备 `supports` capability 暴露给业务 Tool / Task。
- 图片、音频、视频、深度图等大字节数据必须走数据流或资产服务，不放进控制信令 JSON。
- 设备开发者只需要实现注册、能力声明、控制事件处理和数据流读写，不应该理解或依赖 Agent Core 内部实现。
- `legacy/` 中的旧路径、旧协议和旧配置名不能直接复制到主线代码；借鉴旧逻辑前先确认当前公开 API 和文档。

## Tool / Task 规则

一次性、短生命周期动作写 Tool；持续运行、订阅数据流、维护状态或后台流程写 Task。

公开导入优先使用：

```python
from realtime_agent import BaseTask, BaseTool, TaskContext, ToolContext, ToolResult
```

常用能力：

- `context.devices.sensors.rgb.one()`：请求单帧 RGB 资产。
- `context.devices.actuators.vibrator.one()`：请求震动等执行器。
- `context.devices.commands.call()`：发送远程命令并等待端侧回报。
- `context.output.say()`：生成用户可听输出。
- `context.assets.get()`：读取资产。

新增能力时先判断 Tool 还是 Task，再写清端侧能力需求，确认设备能力文件，补测试或可复现联调流程，并检查 `runs/` 产物。

## 协议规则

当前设备能力文件以结构化 `supports` 为准。新增或修改协议时必须同步更新 schema、文档、参考端和测试。

远程命令事件只使用：

- `command.requested`
- `command.accepted`
- `command.progress`
- `command.completed`
- `command.failed`

传感器数据流控制只使用：

- `stream.control.open.requested`
- `stream.control.close.requested`

WebSocket stream 正式入口只使用：

- `/ws/stream/audio/input`
- `/ws/stream/audio/output`
- `/ws/stream/visual/input`

不要新增临时协议名或旧路径兼容说明来绕过 schema。

## 测试规则

完整测试策略、分层命令和适用范围见 [docs/testing.md](docs/testing.md)。执行任务时按影响面选择测试：

- 修改协议数据结构：先跑 P0 协议资产检查。
- 修改 server / device 对事件的处理动作：必须跑 L1。
- 修改真实模型 provider：跑 L2。
- 修改应用或端侧参考工程：跑 L3，并说明真机、模拟器、构建或契约测试的验证层级。

测试编写要求：

- 测试文件命名为 `test_*.py`。
- 新测试用中文 docstring 写明测试目标、测试方法和预期结果。
- 测试目的是暴露问题和验证真实功能，不是为了把测例跑绿而放宽断言。
- 涉及跨设备功能时，必须提供本地可复现联调流程和观察点。

不要把“协议资产检查通过”写成“server/device 行为已验证”，也不要把“契约测试通过”写成“真机已验证”。

## 运行产物和排障

`runs/` 是主要排障证据目录，默认位于应用目录下，例如 `examples/simple-agent-server/runs`。详细文件结构和排查顺序见 [agent-server/docs/how-to/运行产物排查说明.md](agent-server/docs/how-to/运行产物排查说明.md)。

排障时优先用真实运行证据定位：

- 模型没反应：看 `events.jsonl`、`stream-events.jsonl`、`agent-events.jsonl`、根目录 `system-events.jsonl`。
- 模型没拿到工具或上下文：看 `model-request.json`。
- 工具行为不符合预期：看 `tool-events.jsonl` 和相关资产文件。
- 播放、打断、输出异常：看 `output-decisions.jsonl`、`playback-decisions.jsonl`、`/api/debug/playback`。
- 设备没有收到事件：看根目录 `control-routes.jsonl`。

`runs/`、日志、真实用户音频、图片和视频不能提交。

## 日志和配置

- 协助排查的日志使用 `DEBUG`。
- 用户可见或关键状态使用 `INFO`。
- 降级、超时、协议不一致使用 `WARNING` 或 `ERROR`。
- 本地开发优先支持在配置中打开 DEBUG，不要把临时 `print()` 留在主线代码。
- 新增配置项必须补默认值、示例配置、文档说明和测试。
- 不要提交 API Key、设备 token、Wi-Fi 密码、`.env`、本地 `AppConfig.json` 或硬件私有配置。

## 文档规则

- 写文档是为了记录重要决策、协议和联调方法，不是堆文字。
- 复杂架构、流程、时序优先使用 PlantUML。
- 面向开源社区的入口文档优先说明项目价值、推荐路径和当前可运行链路。
- README / docs / agent-server docs / devices docs 中重复的信息，应通过链接引用权威文档，不要复制长段命令或文件表。
- 文档中的测试结果必须来自真实命令结果，不能只写设计预期。

## 代码风格

- 使用 Python 3.11+。
- 遵守现有包边界，不新增全局硬编码路径。
- 公共 SDK API 保持类型清晰，避免让示例应用依赖内部实现细节。
- 类、函数、测试新增注释和 docstring 使用中文，说明功能、主要逻辑、参数、返回值和异常情况。
- 临时诊断脚本或一次性排障代码要轻量，任务结束后删除，避免混入架构代码。
- 复杂或不确定实现先查文档、社区或成熟方案；简单能力可以在依赖成本和自研复杂度之间平衡。
- 不要为了测试通过而牺牲真实功能语义。

## Git 和提交

- 提交信息使用简短中文。
- 不允许直接 push 任意分支到远程，除非用户明确要求。
- 提交保持聚焦，不把无关格式化、运行产物和本地配置混进同一提交。
- 移动文件使用 `git mv`。
- 新工具如果产生缓存、构建产物、日志或媒体文件，必须同步更新 `.gitignore`。

## AI 代理工作准则

开始改代码前先确认任务属于哪一层：

- SDK 核心能力：改 `agent-server/realtime_agent/`，补 `agent-server/protocol-tests/`，必要时补 `agent-server/unit-tests/`。
- Device SDK 或端侧参考：改 `devices/`、`examples/device_app_demo/ios/` 或 `dev-support/devices/`，补端侧契约或联调说明。
- 示例和开发支持能力：改对应 `examples/<app>/agent-server/capabilities/` 或 `dev-support/`。
- 文档或协议：同步更新 docs、schema、测试和示例配置。

遇到多设备、模型、ASR、TTS、数据流、播放仲裁、工具调用问题时，不要只凭命名推断实现状态；要用代码位置、测试命令、运行产物和日志说明真实链路。

完成后说明：

- 改了哪些文件。
- 影响 SDK、示例应用、参考端还是文档。
- 跑了哪些测试或检查。
- 如果没有跑某些关键测试，说明原因和建议的补充验证。
