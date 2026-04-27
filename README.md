# OpenAI Glasses Demo 2

本仓库现在按两个工作方向组织：

1. [openaiglass-sdk](./openaiglass-sdk)：眼镜、手机、服务器三端开发框架。这里放 SDK 源码、三端协议、统一模型、运行时、日志、异常、Task、Tool、全局上下文、硬件能力抽象、SDK 测试和 SDK 架构文档。
2. [openaiglass-for-blind](./openaiglass-for-blind)：基于 SDK 开发的盲人 AI 眼镜真实场景工程。这里放需求、阶段计划、验收文档、find_object 等业务能力、手机/眼镜端工程、设备级回放数据和跨设备联调配置。

详细边界见 [工作边界说明.md](./工作边界说明.md)。

## 常用入口

| 入口 | 说明 |
| --- | --- |
| [openaiglass-sdk/server-python](./openaiglass-sdk/server-python) | Python SDK 包源码，发布包名为 `openaiglasses-sdk`。 |
| [openaiglass-sdk/phone-ios](./openaiglass-sdk/phone-ios) | iOS 通用手机 SDK 运行时。 |
| [openaiglass-sdk/glass-esp32](./openaiglass-sdk/glass-esp32) | ESP32 通用眼镜 SDK 运行时。 |
| [openaiglass-sdk/docs](./openaiglass-sdk/docs) | SDK 架构、协议、模型、运行时和发布文档。 |
| [openaiglass-sdk/tests](./openaiglass-sdk/tests) | SDK 单元、集成、公共契约和兼容性测试。 |
| [openaiglass-for-blind](./openaiglass-for-blind) | 盲人 AI 眼镜真实场景工程，包含业务能力、三端工程、设备级回放资产和 SDK 使用说明。 |
| [openaiglass-for-blind/host](./openaiglass-for-blind/host) | 盲人 AI 眼镜产品宿主，包含很薄的服务端、手机端和眼镜端入口。 |
| [openaiglass-for-blind/capabilities](./openaiglass-for-blind/capabilities) | 盲人 AI 眼镜业务能力集合。 |
| [openaiglass-for-blind/scripts](./openaiglass-for-blind/scripts) | 业务层薄包装、配置同步和联调检查脚本；三端启动优先使用 SDK `openaiglass` 命令。 |

## 常用命令

```bash
openaiglass server local start --app-module host.server.main --app-root openaiglass-for-blind
openaiglass phone open --app-root openaiglass-for-blind
openaiglass glass firmware --repo-root .
uv run python openaiglass-for-blind/scripts/run_sdk_preflight.py --report logs/sdk-preflight-current.json
uv run python openaiglass-sdk/scripts/run_sdk_package_check.py
uv run python -m pytest openaiglass-sdk/tests -q
```

跨设备联调前先看 [openaiglass-sdk/docs/sdk-design/SDK真机联调前检查与联调步骤.md](./openaiglass-sdk/docs/sdk-design/SDK真机联调前检查与联调步骤.md)。
