# 协议数据资产

本目录保存协议数据结构相关资产。`version.json` 记录数据结构协议版本，`fixtures/` 用于跨语言 SDK、Server SDK 和协议资产检查。

新增或修改协议数据时，必须同步更新：

1. `protocol/docs/protocol.md`。
2. `protocol/data/fixtures/` 正例和反例。
3. `protocol/protocol-tests/` 中对应检查。
4. 依赖该协议的 Server SDK / Device SDK L1 行为测试。
