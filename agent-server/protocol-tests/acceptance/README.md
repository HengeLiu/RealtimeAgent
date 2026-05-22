# acceptance 测试

本目录覆盖开发者可见契约、架构边界和文档约束，主要用于防止 SDK / 示例应用退回旧设计。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_architecture_design_contract_acceptance.py` | 验证架构设计文档中的关键约束仍和代码对应。 |
| `test_audio_session_contract.py` | 验证音频 session 设计契约。 |
| `test_auto_discovery_developer_contract.py` | 验证开发者工具 / 任务自动发现契约。 |
| `test_developer_usable_gate.py` | 验证开发者可用性门禁。 |
| `test_indirect_device_context_contract.py` | 验证业务代码通过 Context API 间接访问设备能力。 |
| `test_next_docs_contract.py` | 验证 next 阶段文档契约。 |
| `test_no_internal_service_usage_contract.py` | 验证示例应用不直接依赖 SDK 内部服务。 |
| `test_p0_foundation_contract.py` | 验证 P0 基础能力契约。 |
| `test_protocol_document_contract.py` | 验证协议文档、代码映射和变更 checklist 完整。 |
| `test_task_device_stream_contract.py` | 验证 Task 与设备 stream 的边界契约。 |
