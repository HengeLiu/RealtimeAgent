# realtime-agent 文档目录

`realtime-agent` 是一套面向多端设备的语音 Agent SDK。它由 Server SDK、Device SDK 和标准通讯协议组成，用于构建语音优先、多设备协作、实时 stream 驱动的 AI Agent 应用。项目内部使用事件协议连接 server 和 device，但社区开发者主要面对的是 Tool、Task、Context API、Device SDK 能力声明和 stream API。

它适合智能眼镜、手机协作、ESP32 或其他端侧传感器设备参与的应用原型和产品化探索。仓库中的 `browser-glass`、`python-phone` 等属于开发/测试支持组件：它们会以 Device 形态接入协议，但不是 SDK 预设的正式设备类型。

本文档目录面向社区开发者，目标是帮助开发者快速判断项目是否适合自己、跑通第一个样例，并理解如何扩展 Tool、Task 和设备能力。

## 先读什么

1. [项目介绍](getting-started/what-is-realtime-agent.md)：了解项目定位、代码架构、关键能力和典型使用方式。
2. [快速开始](getting-started/quickstart.md)：准备环境，启动 server，连接开发/测试支持组件。
3. [第一个 Tool 和 Task](tutorials/build-first-capability.md)：理解业务能力如何接入 Agent。
4. [跨设备本地联调](how-to/cross-device-local-debug.md)：按 server、开发支持组件、真实端侧参考工程的顺序做联调。
5. [CLI 参考](reference/cli.md)：查看常用 `realtime-agent.*` 命令。

## 社区开发者文档

### Getting Started

- [项目介绍](getting-started/what-is-realtime-agent.md)
- [快速开始](getting-started/quickstart.md)

### Tutorials

- [第一个 Tool 和 Task](tutorials/build-first-capability.md)

### How-to

- [跨设备本地联调](how-to/cross-device-local-debug.md)
- [测试体系说明](testing.md)
- [设备能力与 Context API 开发说明](../agent-server/docs/how-to/设备能力开发说明.md)
- [runs 目录产物说明](../agent-server/docs/how-to/运行产物排查说明.md)

### Reference

- [项目结构](reference/project-layout.md)
- [CLI 参考](reference/cli.md)
- [端侧 App 接入指南](reference/device-app-integration.md)
- [设备事件行为标准](reference/device-event-behavior.md)
- [端侧 SDK 事件行为实现蓝图](reference/device-sdk-event-blueprint.md)
- [Swift 端 Device SDK 与 Device App 开发计划](reference/swift-device-sdk-and-app-development-plan.md)
- [realtime-agent 通讯协议](../protocol/docs/protocol.md)
- [Context 与设备 API 设计说明](../agent-server/docs/reference/上下文设备接口设计.md)

### Community

- [贡献指南](community/contributing.md)

## 内部设计文档

SDK 内部设计记录位于 [agent-server/docs](../agent-server/docs/)，示例项目设计记录位于各 `examples/<project>/docs` 目录。这些文档可以公开阅读，但不作为稳定 API 或稳定使用入口。

- [多语言端侧通讯 SDK 设计文档](internal/multilanguage-device-sdk-design.md)
- [端侧协议盘点和冻结候选](internal/device-protocol-inventory.md)
- [回归测试分层设计文档](internal/regression-test-strategy.md)
