# conversation 测试

本目录覆盖 Server SDK 的 conversation runtime、Agent Core 与模型适配边界。测试可使用 fake provider，但不 mock SDK 内部核心流程。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_agent_core_recovery.py` | 验证 conversation Agent Core 错误恢复和降级输出。 |
| `test_conversation_runtime.py` | 验证 Omni / VL conversation runtime 装配和配置传递。 |
| `test_context_compiler.py` | 验证模型上下文、工具 schema、历史消息和记忆片段编排。 |
| `test_context_prompt_registry.py` | 验证 prompt registry 与上下文 prompt 注入。 |
| `test_dashscope_asr_adapter.py` | 验证 DashScope ASR adapter 的本地 contract 和边界处理。 |
| `test_provider_degradation_policy.py` | 验证 provider 降级策略、mock fallback 边界和错误暴露。 |
| `test_omni_agent_core.py` | 验证 Omni Realtime Agent Core、Qwen adapter、工具桥、打断和并发限流。 |
| `test_realtime_provider_tool_bridge.py` | 验证 Realtime provider tool call 到 ToolGateway 的桥接。 |
| `test_vision_agent_tool_loop_async.py` | 验证 Vision Realtime Agent Core 的异步 tool loop、消息回填和恢复路径。 |
