# host/glass-playback

本目录放 `glass-playback` 设备级虚拟眼镜宿主配置。

`glass-playback` 是一台独立启动、独立注册、按真实 glass 协议连接服务端的 Python 虚拟设备。它不是组件级测试 runner 的子组件，也不属于 `testdata`。

目录约定：

| 子目录 | 职责 |
| --- | --- |
| [config](./config) | 存放 `glass-playback` 设备配置，例如设备编号、配对令牌、服务端地址、触发音频路径、传感器资产路径和执行器策略。 |

音频、视频、图片和传感器原始资产仍放在 [../../testdata](../../testdata) 对应目录下。配置文件只引用这些资产。
