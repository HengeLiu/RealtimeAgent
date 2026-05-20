# for-blind-app 回放测试

本目录是 L3 真实样例回放测试入口。测试输入来自 `testdata/` 下的真实音频、图片或视频样例，用于验证应用链路是否能在无真机的情况下复现关键场景。

回归命令：

```bash
uv run python -m pytest examples/for-blind-app/replay-tests -q
uv run python -m pytest -m replay -q
```

新增用例规则：

- 优先使用 `testdata/` 中可复查的真实样例。
- 断言应用输出、关键 artifact 和错误诊断。
- 不用纯 mock 替代场景输入；mock 只用于隔离外部不稳定依赖。
