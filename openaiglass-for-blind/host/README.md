# host

本目录放盲人 AI 眼镜产品宿主。宿主只负责启动 SDK 运行时、读取产品配置、注册业务能力和承载平台工程，不直接实现具体业务能力。

| 子目录 | 职责 |
| --- | --- |
| [server](./server) | 服务端宿主入口，装配 SDK 并注册能力。 |
| [phone](./phone) | 手机端宿主工程。 |
| [glass](./glass) | 眼镜端宿主工程。 |
| [glass-playback](./glass-playback) | 设备级虚拟眼镜宿主，存放 `glass-playback` 配置。 |

具体业务能力放在 [../capabilities](../capabilities)。
