# Python Device SDK 单元测试

本目录放 Python Device SDK 的单元测试或静态边界测试。它不承担 server/device 协议行为互认验证。

当前范围：

| 目录 | 说明 |
| --- | --- |
| `static/` | 检查端侧 SDK 不依赖 server 内部运行时对象。 |

回归命令：

```bash
uv run python -m pytest devices/python/unit-tests -q
```
