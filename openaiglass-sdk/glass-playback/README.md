# glass-playback

`glass-playback` 是设备级虚拟眼镜运行时，与 `server-python`、`phone-ios`、`glass-esp32` 同级。

它不是服务端 SDK 子模块，也不是组件级测试 runner。它按真实 glass 设备协议连接服务端，完成注册、心跳、语音会话、触发音频上行、抓拍回传、视频帧推送和执行器记录。

统一启动入口仍由 SDK CLI 提供：

```bash
openaiglass.glass.start --runtime playback \
  --config openaiglass-for-blind/host/glass-playback/config/glass.water_cup.json \
  --repo-root .
```

配置文件放在业务工程 `openaiglass-for-blind/host/glass-playback/config`，音频、图片、视频和传感器资产放在 `openaiglass-for-blind/testdata`。
