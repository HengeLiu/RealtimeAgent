# 协议目录

本目录保存 server 和 device 共同依赖的协议资产。协议不承担运行时行为，只提供可观测、可版本化、可复用的输入。

| 目录 | 说明 |
| --- | --- |
| `docs/` | 协议正式说明入口。 |
| `data/` | 协议数据结构、golden fixture 和反例 fixture。 |
| `behavior/` | 事件处理规范和版本说明。 |
| `unit-tests/` | 协议资产辅助代码的单元测试。 |
| `protocol-tests/` | P0 协议资产检查。 |

回归入口：

```bash
uv run python -m pytest protocol/protocol-tests -q
```
