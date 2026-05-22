# runtime 测试

本目录覆盖 Server SDK 运行时服务，强调“协议输入 -> SDK 行为 -> 事件 / artifact 输出”的系统级测试。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_audio_pipeline_processors.py` | 验证音频处理器、重采样、归一化等 pipeline 边界。 |
| `test_audio_session_lifecycle.py` | 验证音频 session 打开、关闭、超时和状态转移。 |
| `test_continuous_dialog_state.py` | 验证连续对话状态和活跃时间更新。 |
| `test_control_service.py` | 验证控制事件注册、订阅、路由和投递。 |
| `test_conversation_memory_service.py` | 验证对话记忆服务的读写和提示词片段。 |
| `test_device_registration_management.py` | 验证设备注册、在线状态和能力索引。 |
| `test_memory_service.py` | 验证长期记忆存储和查询行为。 |
| `test_model_request_logging.py` | 验证模型请求、provider 事件和终端日志可观测性。 |
| `test_phase2_providers_output.py` | 验证 provider 输出和运行产物记录。 |
| `test_playback_interrupt_policy.py` | 验证播放打断、队列和优先级策略。 |
| `test_progress_audio.py` | 验证工具 / 任务进度音频策略。 |
| `test_runs_layout.py` | 验证 runs 目录布局和关键 artifact 写入。 |
| `test_server_sdk_protocol_integration.py` | 用协议事件和 stream chunk 驱动 Server SDK 完整 Text turn。 |
| `test_stream_and_audio_pipeline.py` | 验证 stream 服务与音频 pipeline 组合路径。 |
| `test_streaming_tts_runtime.py` | 验证 streaming TTS 输出和收尾路径。 |
| `test_task_engine_persistence.py` | 验证 Task Engine 持久化状态。 |
| `test_task_engine_scheduler.py` | 验证 Task 调度、启动、取消和并发限制。 |
| `test_task_manage_tool.py` | 验证 Task 管理工具的启动、查询和停止操作。 |
| `test_task_signal_bridge.py` | 验证 task signal 与协议事件桥接。 |
| `test_tool_spec_schema.py` | 验证 ToolSpec schema 生成、输入校验和调用。 |
| `test_typed_device_context_api.py` | 验证 typed device context API 下发协议事件并消费回执。 |
| `test_voice_session_modes.py` | 验证文本 / realtime 语音 session 模式选择。 |
