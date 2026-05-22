# sdk 测试

本目录是 L1 Server SDK 测试入口，主要验证 SDK 面对协议事件、stream chunk、fake provider 输出时的系统级响应。

| 目录 | 测试目标和范围 |
| --- | --- |
| `agent_core/` | Agent Core、provider 降级、上下文编排、tool bridge 和 Realtime 逻辑。 |
| `runtime/` | control、stream、audio pipeline、output、tool、task、memory、runs 等运行时服务。 |
| `interop/` | Server SDK 与 Device SDK 通过真实 WebSocket 的互操作闭环。 |
| `config/` | 配置加载、字段同步和默认值契约。 |
| `extensions/` | MCP、Skill 等扩展能力的 SDK 边界测试。 |
