# audio-server 测试目录

本目录收纳 Server SDK、协议、模型接入、CLI 和开发者契约相关测试。测试按回归层级与功能边界分类，避免所有脚本堆在根目录。

## 子目录

| 目录 | 测试目标和范围 |
| --- | --- |
| `acceptance/` | 面向开发者契约、架构边界和文档约束的验收测试。 |
| `protocol/` | L0 协议层测试，覆盖事件信封、schema、fixture、stream codec 和协议状态机。 |
| `sdk/` | L1 Server SDK 测试，覆盖 Agent Core、运行时服务、配置、扩展和互操作闭环。 |
| `model_provider/` | L2 真实模型 provider smoke 和 artifact 生成辅助。 |
| `cli/` | CLI、打包、发布、配置同步和文档命令测试。 |
| `helpers/` | 测试专用 harness 和 fake provider，不属于 SDK 运行时代码。 |
| `fixtures/` | 测试输入样例，例如 provider 固定音频。 |
