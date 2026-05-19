# extensions 测试

本目录覆盖可选扩展能力，确保 MCP、Skill 等扩展不破坏 SDK 核心边界。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_mcp_external_server_smoke.py` | 验证外部 MCP server smoke 和调用边界。 |
| `test_mcp_gateway.py` | 验证 MCP Gateway 配置、工具发现和调用。 |
| `test_skill_service.py` | 验证 Skill 服务加载、工具策略和上下文集成。 |
