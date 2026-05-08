# host/phone-mock

本目录放 `phone-mock` 设备级虚拟手机宿主配置。

`phone-mock` 是一台独立启动、独立注册、按真实 phone 协议连接服务端的 Python 虚拟设备。它用于承载手机侧 Python mock 测试代码，功能开发者从服务端视角看到的是一台在线 phone 设备，而不是组件级测试 runner。

目录约定：

| 子目录 | 职责 |
| --- | --- |
| [config](./config) | 存放 `phone-mock` 设备配置，例如设备编号、配对令牌、服务端地址、任务类型和 mock 事件输出。 |

