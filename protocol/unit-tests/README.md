# 协议单元测试

本目录只放协议资产辅助代码的单元测试，例如未来新增的 fixture 生成器、schema 生成器或版本解析工具。

测试范围：

- 不启动 server。
- 不连接 device。
- 不验证 SDK 收到事件后的运行时行为。

当前协议核心资产以文档和 JSON/YAML fixture 为主，因此主要回归入口是 `protocol/protocol-tests/`。
