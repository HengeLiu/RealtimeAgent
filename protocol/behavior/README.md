# 事件处理规范

本目录用于沉淀事件处理规范。它描述 server 和 device 收到特定协议事件后，应该返回什么事件、触发什么处理过程、更新什么状态和写出什么运行产物。

这里不是测试执行入口。真正的行为验证分别落在：

- `agent-server/protocol-tests/`
- `devices/python/protocol-tests/`

事件处理规范版本记录在 `version.json`，与数据结构协议版本独立演进。
