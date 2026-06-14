# Changelog

## 0.1.0rc1

### 架构变更点

- 新增 conversation 音视频对话运行时方向，支持 Omni Manual 与 VL 链路逐步统一到 `SpeechInputDelta` 输入模型。
- 引入 `agent.conversation.runtime` 配置，保留 legacy fallback，便于新旧链路并行验证。
- 补充 Python Device SDK 最小公共入口，确保 server SDK、参考端和 interop 测试共享同一套控制事件与 stream 编解码协议。

### Context API

- 保持 `ToolContext` 作为业务能力访问设备、资产、输出和上下文的边界。
- conversation 重构不改变业务 Tool 直接使用的 Context API。

### package-check

- `realtime-agent.sdk.package-check` 覆盖 entry point 导入、公开 API、wheel 构建、wheel 安装、wheel 内容、editable install、端侧源码边界和 release candidate 记录。
