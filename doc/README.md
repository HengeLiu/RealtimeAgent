# 项目文档入口

本文档是当前 `doc/` 目录的开发者阅读入口。它只描述当前文档如何阅读，具体实现状态以 [当前实现状态.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/当前实现状态.md) 和代码为准。

## 推荐阅读顺序

1. [当前实现状态.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/当前实现状态.md)：先确认当前代码已经实现什么、入口在哪里、哪些内容仍是计划。
2. [restriction/软件架构设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/restriction/软件架构设计.md)：理解最初的产品目标、三端职责和总体架构。
3. [structure-design/统一通信协议信封设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/统一通信协议信封设计.md)：理解控制消息、设备注册和跨端协议基础。
4. [structure-design/agent-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/agent-core设计.md)：理解服务端智能体运行时。
5. [structure-design/backend-task-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/backend-task-core设计.md)：理解后台任务生命周期。
6. [stage1/plan](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/plan)：查看第一期分阶段目标。
7. [stage1/develop](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop)：按 Phase 查看每次实施、联调和测试结果。
8. [sdk-design/SDK开发者快速开始.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design/SDK开发者快速开始.md)：进入 SDK 使用面。
9. [sdk-design/PythonSDK打包与发布流程.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design/PythonSDK打包与发布流程.md)：查看 SDK 包构建、TestPyPI 和 PyPI 发布流程。
10. [stage2/plan/第二期-SDK核心运行时与开发者扩展面产品化开发计划.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage2/plan/第二期-SDK核心运行时与开发者扩展面产品化开发计划.md)：理解 SDK 产品化方向。
11. [stage2/plan/第二期SDK最终验收方案.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage2/plan/第二期SDK最终验收方案.md)：执行第二期完成后的最终验收。
12. [../sdk/python/README.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/README.md)：查看 `pip install openaiglasses-sdk` 后的包级使用说明。

## 目录职责

| 目录 | 面向读者 | 内容边界 |
| --- | --- | --- |
| [restriction](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/restriction) | 产品、架构、开发负责人 | 原始约束、功能设想、总体架构和文档要求。 |
| [structure-design](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design) | 服务端与端侧开发人员 | 协议、消息、媒体帧、agent-core、backend-task-core、Skill Runtime 等结构设计。 |
| [stage1/plan](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/plan) | 项目管理与开发人员 | 第一期各阶段计划，说明为什么按这些 Phase 落地。 |
| [stage1/develop](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop) | 直接改代码和联调的人 | Phase A 到 K 的实施文档、联调说明、测试结果和遗留问题。 |
| [stage2/plan](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage2/plan) | SDK 方向开发人员 | SDK 产品化计划、真实音频样例回归和第二期阶段安排。 |
| [sdk-design](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design) | SDK 使用者与 SDK 维护者 | SDK 产品形态、快速开始、测试架构和真机联调步骤。 |
| [feature-design](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/feature-design) | 迁移开发人员 | 旧项目能力分析和迁移参考。 |
| [experimental](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/experimental) | 方案调研人员 | Spike、调研、实验性 demo，不作为当前实现状态的唯一依据。 |

## 当前代码对应关系

| 主题 | 代码入口 | 主要文档 |
| --- | --- | --- |
| 服务端启动与 HTTP 路由 | [sdk/python/app/main.py](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/app/main.py)、[sdk/python/api/http_server.py](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/api/http_server.py) | [当前实现状态.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/当前实现状态.md) |
| 控制连接、注册、绑定、抓拍、视频任务 | [sdk/python/api/ws/control_runtime.py](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/api/ws/control_runtime.py) | [PhaseI-手机接入与绑定实施文档.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop/PhaseI-手机接入与绑定实施文档.md)、[PhaseJ-真实视频数据面实施文档.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop/PhaseJ-真实视频数据面实施文档.md) |
| 语音链路与 TTS 播放 | [sdk/python/runtime/voice_runtime.py](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/runtime/voice_runtime.py) | [PhaseC非实时语音对话实施文档.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop/PhaseC非实时语音对话实施文档.md)、[PhaseE-流式交互与拍照主链路改造实施文档.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop/PhaseE-流式交互与拍照主链路改造实施文档.md) |
| agent-core 与工具面 | [sdk/python/agent_core](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/agent_core) | [agent-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/agent-core设计.md)、[PhaseE-agent输入与工具面收敛实施文档.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop/PhaseE-agent输入与工具面收敛实施文档.md) |
| backend-task-core | [sdk/python/backend_task_core](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/backend_task_core) | [backend-task-core设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/backend-task-core设计.md)、[PhaseF-backend-task-core最小闭环实施文档.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage1/develop/PhaseF-backend-task-core最小闭环实施文档.md) |
| SDK 与官方 example | [sdk/python/openaiglasses](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/openaiglasses)、[example](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/example) | [sdk-design](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design)、[第二期-SDK核心运行时与开发者扩展面产品化开发计划.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/stage2/plan/第二期-SDK核心运行时与开发者扩展面产品化开发计划.md) |
| Python SDK 打包与安装 | [sdk/python/pyproject.toml](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/pyproject.toml)、[script/run_sdk_package_check.py](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/script/run_sdk_package_check.py) | [SDK开发者快速开始.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design/SDK开发者快速开始.md)、[PythonSDK打包与发布流程.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/sdk-design/PythonSDK打包与发布流程.md)、[sdk/python/README.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/sdk/python/README.md) |

## 文档维护规则

1. 新增功能先更新对应 `stage*/develop` 实施文档，再按需要更新结构设计或 SDK 文档。
2. 文档里写“已实现”前，必须能指向代码入口、测试命令或真机联调结果。
3. 实验性结论放在 `experimental`，不要直接覆盖当前实现状态。
4. 跨设备能力要同时写清服务端、手机端、眼镜端启动顺序和日志观察点。
5. 流程图和时序图优先使用 PlantUML。
