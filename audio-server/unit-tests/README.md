# Server SDK 单元测试

本目录放 Server SDK 的单元测试或轻量静态边界测试。它不承担系统级事件行为一致性验证。

当前范围：

| 目录 | 说明 |
| --- | --- |
| `cli/` | CLI、打包、公开 API、文档命令和进程启动边界。 |

回归命令：

```bash
uv run python -m pytest audio-server/unit-tests -q
```

新增用例规则：

- 测试目标应聚焦单个模块或轻量边界。
- 如果输入是协议事件并断言 SDK 响应，应放到 `audio-server/protocol-tests/`。
- 如果依赖真实模型 provider，应放到 `audio-server/model-provider-tests/`。
