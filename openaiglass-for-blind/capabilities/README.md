# capabilities

本目录放真实盲人场景的业务能力实现。每个能力可以包含服务端 Tool、服务端 Task、手机侧处理器、手机侧任务和端侧插件。设备级数据回放由独立的 `glass-playback` 设备组件完成，能力目录不再提供组件级回放处理器。

当前能力：

| 能力 | 职责 |
| --- | --- |
| [find_object](./find_object) | 通过眼镜视频流和手机侧处理器完成找物体闭环。 |
| [traffic_light](./traffic_light) | 通过眼镜视频流和手机侧处理器识别红绿灯状态，并向眼镜提交过街提示。 |
| [navigation](./navigation) | 通过 SDK MCP 入口调用高德地图或回放 mock 准备步行路线，并创建可查询、可取消的导航任务。 |
| [timer](./timer) | 通过 SDK 托管任务创建、查询、取消和完成计时器，并在业务层验证最小后台倒计时。 |
| [search](./search) | 通过 SDK MCP 入口搜索公开网页信息，供 Agent 组织回答。 |
