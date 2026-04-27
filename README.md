# OpenAI Glasses Demo 2

本项目现在按两个边界清晰的方向组织：

1. [openaiglass-sdk](./openaiglass-sdk)：眼镜、手机、服务器三端开发框架，负责协议、统一模型、设备绑定、通讯、日志、异常处理、大模型运行时、Tool/Task/Skill、全局上下文、硬件能力抽象、回放测试和联调工具。
2. [openaiglass-for-blind](./openaiglass-for-blind)：基于 SDK 的盲人 AI 眼镜真实场景工程，负责找物、红绿灯、导航、计时器等业务能力，以及业务侧三端宿主配置和功能文档。

两个目录通过 [openaiglass-for-blind/SDK安装与能力开发指南.md](./openaiglass-for-blind/SDK安装与能力开发指南.md) 和 [openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md](./openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md) 沟通。功能开发团队默认只改 `openaiglass-for-blind`，不要在业务迭代中补 SDK 层系统能力。

## 目录概览

| 目录 | 说明 |
| --- | --- |
| [openaiglass-sdk/server-python](./openaiglass-sdk/server-python) | Python SDK 包源码，发布包名为 `openaiglasses-sdk`，导入名为 `openaiglasses`。 |
| [openaiglass-sdk/phone-ios](./openaiglass-sdk/phone-ios) | iOS 通用手机运行时，负责注册、心跳、视频接收、手机任务承载和结果上报。 |
| [openaiglass-sdk/glass-esp32](./openaiglass-sdk/glass-esp32) | ESP32 通用眼镜运行时，负责 WiFi、控制连接、音频、摄像头和端侧命令。 |
| [openaiglass-sdk/phone-mock](./openaiglass-sdk/phone-mock) | 按真实 phone 协议运行的 Python 虚拟手机设备。 |
| [openaiglass-sdk/glass-playback](./openaiglass-sdk/glass-playback) | 按真实 glass 协议运行的眼镜回放设备。 |
| [openaiglass-for-blind/capabilities](./openaiglass-for-blind/capabilities) | 当前业务能力：`find_object`、`traffic_light`、`navigation`、`timer`。 |
| [openaiglass-for-blind/host](./openaiglass-for-blind/host) | 盲人业务工程的服务端、手机端、眼镜端薄宿主和本地配置。 |
| [openaiglass-for-blind/docs](./openaiglass-for-blind/docs) | 业务计划、开发记录、当前状态、真机联调和阻塞点文档。 |

## SDK 简要现状

当前 SDK 已经能支撑功能团队继续开发：服务端可通过 `OpenAIGlassesSDK` 注册 `BaseTool`、`BaseTask`、`BasePhoneTask`、`BasePhoneProcessor`、`BaseSensorProvider` 和 MCP Adapter；`DeviceGroupContext` 提供设备查询、抓拍、手机视频链路、手机任务、通知、MCP 和 SDK 托管任务等公开入口；CLI 提供配置同步、服务端启动、手机工程打开/构建、`phone-mock`、眼镜构建/回放、预检和包检查。

仍需 SDK 团队后续补齐的系统能力包括实时语音打断、普通回复端到端真流式、公网/NAT peer-link 治理、生产级任务持久化恢复、手机端模型资源仲裁、真实外部服务治理，以及 iOS/ESP32 发布级包化。详细判断见 [SDK对功能开发支持情况的说明.md](./SDK对功能开发支持情况的说明.md)。

## 功能开发现状

`openaiglass-for-blind` 已经基于 SDK 完成四类样板能力：

| 能力 | 当前状态 |
| --- | --- |
| `find_object` | 服务端 Tool/Task、手机处理器、手机任务和 iOS 插件样例已具备，可启动眼镜到手机视频链路并按手机检测事件完成任务。 |
| `traffic_light` | 已有红绿灯识别 Tool/Task、手机侧处理器和任务样例。 |
| `navigation` | 已有导航准备 Tool、导航 Task、业务侧 mock AMap MCP Adapter、POI/路线准备和事件推进样板。 |
| `timer` | 已有计时器 Tool/Task，用 SDK 托管任务验证创建、查询、取消和完成通知。 |

功能开发的详细入口见 [openaiglass-for-blind/SDK安装与能力开发指南.md](./openaiglass-for-blind/SDK安装与能力开发指南.md)，当前真实实现面见 [openaiglass-for-blind/docs/当前实现状态.md](./openaiglass-for-blind/docs/当前实现状态.md)。

## 常用命令

```bash
uv sync --python 3.11
uv pip install -e openaiglass-sdk/server-python

uv run openaiglass.config.sync --app-root openaiglass-for-blind
uv run openaiglass.server.run --app-module host.server.main --app-root openaiglass-for-blind
uv run openaiglass.phone.mock --config openaiglass-for-blind/host/phone-mock/config/phone.mock.json
uv run openaiglass.glass.start --runtime playback --config <glass-playback.json>
uv run openaiglass.sdk.preflight --report openaiglass-for-blind/logs/sdk-preflight-current.json
uv run python -m pytest openaiglass-sdk/tests -q
```

真实三端联调建议按“同步配置 -> 启动服务端 -> 启动手机或 `phone-mock` -> 启动 ESP32 眼镜或 `glass-playback` -> 触发业务能力 -> 看任务事件和端侧日志”的顺序执行。

## 给功能开发者的下一步建议

1. 新能力优先复用现有四个样板目录，不要直接操作 SDK 内部运行时。
2. 业务代码需要设备、通知、抓拍、视频链路、手机任务、MCP 或后台任务时，只通过 SDK 公开上下文调用。
3. 测试优先使用 `phone-mock`、`glass-playback` 和 `openaiglass.sdk.preflight` 做设备级自动化闭环，再进入真机联调。
4. 如果 SDK 公开能力不能表达业务需求，把问题写入 [架构阻塞点说明与改进建议.md](./openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md)，由 SDK 团队继续优化。
