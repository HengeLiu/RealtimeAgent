# Python Device SDK 协议行为测试

本目录是 L1 Python Device SDK 系统级集成测试入口。测试重点是 Device SDK 面对 server 事件、WebSocket 消息和 stream chunk 时，是否能按事件处理规范调用开发者 handler、发送回执或上传数据。

| 目录 | 测试目标 |
| --- | --- |
| `protocol/` | 事件对象、stream codec 和协议 fixture 消费。 |
| `client/` | DeviceBuilder、WebSocket client contract。 |
| `multilanguage/` | 多语言 SDK fixture 一致性调度。 |

回归命令：

```bash
uv run python -m pytest devices/python/protocol-tests -q
uv run python -m pytest -m device_sdk -q
```

新增用例规则：

- 输入优先使用 `protocol/data/fixtures/`、server 下发事件、WebSocket control 消息或 stream chunk。
- 结果应断言 handler 调用、accepted/progress/completed/failed 回执、stream 上传和连接诊断。
- 如果测试的是 server/device 真实互操作，同时打 `interop` marker，必要时放到 Server SDK 或端侧对应的 interop 子目录。
