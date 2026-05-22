# 协议资产检查

本目录用于 P0 协议资产检查。这里不测试 Server SDK 或 Device SDK 的运行时动作；运行时事件处理行为分别放到各自模块的 `protocol-tests/` 中。

测试范围：

- `protocol/docs/protocol.md` 是否包含协议目标、版本、通道、事件信封、stream 帧格式和变更流程。
- `protocol/data/fixtures/` 中的正例和反例是否能被 schema 或运行时代码识别。
- stream chunk golden binary 是否能被编码器稳定解析。
- 设备能力声明语义是否和当前协议保持一致。

回归命令：

```bash
uv run python -m pytest protocol/protocol-tests -q
uv run python -m pytest -m protocol_spec -q
uv run python -m pytest -m protocol -q
```

新增用例规则：

1. 修改数据结构协议时，先更新 `protocol/docs/protocol.md` 和 `protocol/data/fixtures/`。
2. 正例 fixture 应覆盖跨语言 SDK 都需要消费的稳定输入。
3. 反例 fixture 应覆盖旧协议字段、未知事件名、非法 payload 和非法 stream。
4. 如果变更影响事件处理动作，不在本目录补系统行为测试，而是去 `agent-server/protocol-tests/` 或 `devices/python/protocol-tests/` 补 L1 用例。
