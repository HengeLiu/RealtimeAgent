# openaiglass-sdk

本目录维护三端 SDK 开发框架，目标是隐藏眼镜、手机、服务器协作中的系统性细节，让业务开发者只扩展 Tool、Task、Skill 或手机侧处理器。

Python SDK 本地开发可直接从本目录安装：

```bash
uv pip install -e openaiglass-sdk
```

顶层 `pyproject.toml` 会把 Python 包源码映射到 `server-python`；`server-python` 仍是实际的 Python 模块源码目录。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| [server-python](./server-python) | 服务端 Python SDK 包源码，包含协议、运行时、agent-core、backend-task-core、公开扩展面和测试工具。 |
| [phone-ios](./phone-ios) | iOS 通用手机 SDK 运行时工程，承载注册、控制连接、视频接收和手机任务运行时。 |
| [glass-esp32](./glass-esp32) | ESP32 通用眼镜 SDK 运行时工程，承载控制连接、音频、摄像头和端侧命令处理。 |
| [docs](./docs) | SDK 架构、协议、模型、测试、联调、打包和发布文档。 |
| [tests](./tests) | SDK 单元测试、集成测试、公共契约测试和盲人业务兼容性测试。 |
| [testdata/contracts](./testdata/contracts) | SDK 公共对象与协议金样。 |
| [scripts](./scripts) | SDK 打包检查、契约测试、兼容性测试和音频样例回归入口。 |
| [config](./config) | 服务端本地配置与模板。 |

## 常用命令

```bash
uv run python openaiglass-sdk/scripts/run_sdk_package_check.py
uv run python openaiglass-sdk/scripts/run_sdk_contract_tests.py --pretty
uv run python openaiglass-sdk/scripts/run_sdk_compatibility_tests.py --pretty
uv run python -m pytest openaiglass-sdk/tests -q
```

业务开发者使用 SDK 的说明见 [../openaiglass-for-blind/SDK安装与能力开发指南.md](../openaiglass-for-blind/SDK安装与能力开发指南.md)。
