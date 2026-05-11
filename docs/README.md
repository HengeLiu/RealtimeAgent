# audio-chat 文档目录

`audio-chat` 是一个 server-side Python SDK，用于构建语音优先、多设备协作、实时 stream 驱动的 AI Agent 应用。它适合智能眼镜、手机协作、浏览器参考设备、ESP32 或其他端侧传感器设备参与的应用原型和产品化探索。

本文档目录面向社区开发者，目标是帮助开发者快速判断项目是否适合自己、跑通第一个样例，并理解如何扩展 Tool、Task 和设备能力。

## 先读什么

1. [项目定位](getting-started/what-is-audio-chat.md)：了解 `audio-chat` 解决什么问题，以及它不解决什么问题。
2. [快速开始](getting-started/quickstart.md)：准备环境，启动 server，连接浏览器参考设备。
3. [第一个 Tool 和 Task](tutorials/build-first-capability.md)：理解业务能力如何接入 Agent。
4. [跨设备本地联调](how-to/cross-device-local-debug.md)：按 server、glass、phone、iOS、ESP32 的顺序做联调。
5. [CLI 参考](reference/cli.md)：查看常用 `audio-chat.*` 命令。

## 社区开发者文档

### Getting Started

- [项目定位](getting-started/what-is-audio-chat.md)
- [快速开始](getting-started/quickstart.md)

### Tutorials

- [第一个 Tool 和 Task](tutorials/build-first-capability.md)

### How-to

- [跨设备本地联调](how-to/cross-device-local-debug.md)
- [设备能力与 Context API 开发说明](../audio-server/docs/how-to/device-capability-development.md)
- [runs 目录产物说明](../audio-server/docs/how-to/inspect-runs-artifacts.md)

### Reference

- [项目结构](reference/project-layout.md)
- [CLI 参考](reference/cli.md)
- [Context 与设备 API 设计说明](../audio-server/docs/reference/context-api.md)

### Community

- [贡献指南](community/contributing.md)

## 内部设计与阶段记录

SDK 内部设计记录位于 [audio-server/docs](../audio-server/docs/)，示例项目设计记录位于各 `examples/<project>/docs` 目录。这些文档可以公开阅读，但不作为稳定 API 或稳定使用入口。
