# openaiglass-sdk

本目录维护三端 SDK 开发框架，目标是隐藏眼镜、手机、服务器协作中的系统性细节，让业务开发者只扩展 Tool、Task、Skill 或手机侧处理器。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| [python](./python) | Python SDK 包源码，包含协议、运行时、agent-core、backend-task-core、公开扩展面和测试工具。 |
| [docs](./docs) | SDK 架构、协议、模型、测试、联调、打包和发布文档。 |
| [tests](./tests) | SDK 单元测试、集成测试、公共契约测试和盲人业务兼容性测试。 |
| [testdata/contracts](./testdata/contracts) | SDK 公共对象与协议金样。 |
| [scripts](./scripts) | SDK 打包检查、契约测试、兼容性测试和音频样例回归入口。 |
| [config](./config) | 服务端本地配置与模板。 |
| [server-compat](./server-compat) | 旧 `server/src` 导入路径兼容壳，真实实现仍在 `python/`。 |

## 常用命令

```bash
uv run python openaiglass-sdk/scripts/run_sdk_package_check.py
uv run python openaiglass-sdk/scripts/run_sdk_contract_tests.py --pretty
uv run python openaiglass-sdk/scripts/run_sdk_compatibility_tests.py --pretty
uv run python -m pytest openaiglass-sdk/tests -q
```

业务开发者使用 SDK 的说明见 [../openaiglass-for-blind/SDK安装与能力开发指南.md](../openaiglass-for-blind/SDK安装与能力开发指南.md)。
