# protocol 测试

本目录是 L0 协议层测试入口，只验证通讯契约，不依赖真实模型、网络或真实设备。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_protocol_contracts.py` | 验证事件信封、事件命名、协议对象和基础契约。 |
| `test_protocol_event_abstractions.py` | 验证协议事件抽象对象的构造、序列化和兼容边界。 |
| `test_protocol_schema_examples.py` | 使用 schema 校验协议 golden fixtures 和 invalid fixtures。 |
| `test_protocol_state_machines.py` | 验证 command、input stream、output stream 生命周期状态机。 |
| `test_stream_chunk_codec_contract.py` | 验证二进制 stream chunk 编解码与 golden fixture 兼容。 |
| `test_device_capabilities_semantics.py` | 验证结构化设备能力声明语义和旧字段拒绝策略。 |
