# openaiglass-for-blind

本目录维护“盲人 AI 眼镜”真实场景工程。它基于 [openaiglass-sdk](../openaiglass-sdk) 提供的三端开发框架实现业务能力，不重复实现 SDK 的通信、运行时、日志、异常和上下文底座。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| [docs](./docs) | 产品需求、功能设计、阶段计划、实施记录、验收和迁移文档。 |
| [server](./server) | 盲人业务服务端入口，负责装配 SDK 并注册业务能力。 |
| [capabilities](./capabilities) | 真实盲人场景业务能力实现，例如找物体能力。 |
| [phone](./phone) | 手机端工程，承载注册、视频接收、手机侧任务与业务处理器。 |
| [glass](./glass) | 眼镜端工程，承载传感器采集、控制连接、音频播放和端侧执行。 |
| [testdata](./testdata) | 盲人场景回放数据和业务兼容性场景。 |
| [scripts](./scripts) | 服务端、眼镜端、场景回放、预检、真机联调和配置同步脚本。 |
| [SDK安装与能力开发指南.md](./SDK安装与能力开发指南.md) | 给业务开发者看的 SDK 安装、能力扩展和联调说明。 |

## 开发者入口

1. 先读 [SDK安装与能力开发指南.md](./SDK安装与能力开发指南.md)，理解如何基于 SDK 扩展能力。
2. 再看 [capabilities/find_object](./capabilities/find_object)，这是当前真实业务能力实现。
3. 进入真机联调前执行：

```bash
uv run python scripts/run_sdk_scenario.py --scenario-dir testdata/scenario --pretty
uv run python scripts/run_sdk_preflight.py --report ../logs/sdk-preflight-current.json
bash scripts/sync_sdk_live_config.sh
```

跨设备启动顺序：先启动服务端，再启动手机端 iOS 工程，最后启动或烧录眼镜端工程。
