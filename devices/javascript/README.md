# JavaScript Device SDK

`devices/javascript` 是 realtime-agent 的 JavaScript Device SDK，覆盖浏览器、Node、Electron 和 WebView 端侧。

当前第一版优先支持浏览器端音视频对话：

- 设备注册、心跳和 control WebSocket。
- 音频上行、音频下行、视觉上行三条媒体 WebSocket。
- 浏览器麦克风、speaker、相机默认 adapter。
- speaker 播放 buffer、finish/drain/cancel 状态机。
- `custom.*` 事件和自定义命令语法糖。

运行单元测试：

```bash
cd devices/javascript
npm test
```

设计边界见 [JAVASCRIPT_DEVICE_SDK_DESIGN.md](JAVASCRIPT_DEVICE_SDK_DESIGN.md)。
