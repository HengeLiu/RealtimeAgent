# dev-support 应用测试

本目录放 dev-support 参考端与 server 的应用级或网络级测试。

当前范围：

| 目录 | 测试目标 |
| --- | --- |
| `network/` | Python glass / server 网络回放闭环。 |

回归命令：

```bash
uv run python -m pytest examples/dev-support/app-tests -q
```

新增用例规则：

- 涉及真实 server 网络闭环、跨进程 CLI 或端侧参考组件组合时放到这里。
- 单个端侧组件内部边界优先放到 `examples/dev-support/unit-tests/`。
- 真实样例回放放到 `examples/dev-support/replay-tests/`。
- 真机或人工权限验证放到 `examples/dev-support/hardware-tests/`。
