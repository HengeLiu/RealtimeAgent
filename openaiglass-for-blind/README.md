# openaiglass-for-blind

本目录维护“盲人 AI 眼镜”真实场景工程。它基于 [openaiglass-sdk](../openaiglass-sdk) 提供的三端开发框架实现业务能力，不重复实现 SDK 的通信、运行时、日志、异常和上下文底座。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| [docs](./docs) | 产品需求、功能设计、阶段计划、实施记录、验收和迁移文档。 |
| [host](./host) | 盲人产品宿主，包含很薄的服务端、手机端和眼镜端入口。 |
| [capabilities](./capabilities) | 真实盲人场景业务能力实现，例如找物体能力。 |
| [testdata](./testdata) | 盲人场景设备级回放数据和业务兼容性数据。 |
| [scripts](./scripts) | 兼容旧习惯的薄包装、预检、真机联调和配置同步脚本；主要启动能力由 SDK `openaiglass` 命令提供。 |
| [SDK安装与能力开发指南.md](./SDK安装与能力开发指南.md) | 给业务开发者看的 SDK 安装、能力扩展和联调说明。 |

## 开发者入口

1. 先读 [SDK安装与能力开发指南.md](./SDK安装与能力开发指南.md)，理解如何基于 SDK 扩展能力。
2. 再看 [capabilities/find_object](./capabilities/find_object)，这是当前真实业务能力实现。
3. 进入真机联调前执行：

```bash
uv run python scripts/run_sdk_preflight.py --report ../logs/sdk-preflight-current.json
uv run python scripts/sync_sdk_live_config.py
```

跨设备启动顺序：先用 `openaiglass server local start --app-module host.server.main --app-root openaiglass-for-blind` 启动服务端，再用 `openaiglass phone open --app-root openaiglass-for-blind` 启动手机端入口，最后用 `openaiglass glass firmware --repo-root .` 启动或烧录眼镜端工程。
