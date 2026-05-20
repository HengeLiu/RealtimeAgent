# for-blind-app 应用测试

本目录是 L3 for-blind-app 应用功能测试入口。它验证应用能力、端侧配置、迁移契约和应用级验收，不重复承担协议资产检查和 SDK L1 行为一致性测试。

| 目录 | 测试目标 |
| --- | --- |
| `acceptance/` | 应用迁移、能力模板和视觉任务验收契约。 |
| `capabilities/` | 应用业务工具、任务和 peer video task。 |
| `config/` | 应用启动、配置路径、配置同步和多设备配置。 |
| `endpoints/` | iOS、ESP32、peer video 等端侧参考工程契约。 |
| `fixtures/` | 应用测试专用 fixtures。 |

回归命令：

```bash
uv run python -m pytest examples/for-blind-app/app-tests -q
uv run python -m pytest -m app -q
```

新增用例规则：

- 应用业务工具或任务放 `capabilities/`。
- 端侧参考工程配置放 `endpoints/`。
- 启动配置、路径和环境同步放 `config/`。
- 如果输入是真实音频、图片、视频样例，应放到 `replay-tests/`。
- 如果必须真机或人工权限确认，应放到 `hardware-tests/` 并说明人工验收缺口。
