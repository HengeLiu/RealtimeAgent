# Python Device SDK 测试目录

本目录覆盖 Python 端侧 SDK 的协议、客户端、静态边界和多语言 contract 调度。

| 目录 | 测试目标和范围 |
| --- | --- |
| `protocol/` | 事件信封和 stream codec 协议兼容测试。 |
| `client/` | Device builder 和真实 WebSocket contract。 |
| `static/` | Python Device SDK 静态边界，防止依赖 server runtime。 |
| `multilanguage/` | 调度 TypeScript / Swift / C / Kotlin 等多语言 SDK contract。 |
