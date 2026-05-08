# search 迁移样板

旧能力价值：调用搜索服务或业务知识源，为 Agent 提供可引用的外部信息。

audio-chat 迁移路径：

1. 搜索实现优先封装成 MCP method 或 provider wrapper。
2. Tool 只负责参数整理、调用 MCP、返回摘要和引用。
3. 搜索 Tool 不需要设备通讯时，不使用 `UserDeviceContext`。
4. 搜索结果如果要播报，交给 Agent 或 Output Service。

参考：

- `audio_chat.McpGateway`
- `docs/phase3-migration-guide.md` 的 MCP Adapter 迁移章节。

验收要求：

- 无真实 key 时 fallback 或 skip 必须明确。
- 真实 provider 出错时返回结构化错误。
- 不把网页正文或大文件塞进控制事件 payload。
