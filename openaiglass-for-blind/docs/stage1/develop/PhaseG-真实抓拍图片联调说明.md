# PhaseG 真实抓拍图片联调说明

## 1. 目标

验证以下链路已经打通：

1. 服务端可下发 `sensor.camera.capture`
2. 眼镜端可真实抓拍一张图片
3. 图片可回到 `capture_photo -> 主链路图片理解`

## 2. 服务端启动

在仓库根目录执行：

```bash
LOG_LEVEL=DEBUG LOG_FILE=logs/server.log PYTHONPATH=openaiglass-sdk/server-python uv run python -m app.main --host 0.0.0.0 --port 8765
```

重点观察：

1. `tool.call name=capture_photo`
2. `camera.capture.request`
3. `camera.capture.result`

## 3. 眼镜端联调

建议步骤：

1. 重新拉取依赖并编译 `openaiglass-sdk/glass-esp32`
2. 烧录到 `XIAO ESP32S3 Sense`
3. 打开串口日志

重点观察：

1. `摄像头初始化完成`
2. `开始执行单次抓拍`
3. `单次抓拍完成`

## 4. 语音联调用例

推荐直接说：

1. `看一下我前面有什么`
2. `帮我看看桌子上有什么`

预期：

1. 模型先命中 `capture_photo`
2. 抓拍完成后，主链路模型继续查看真实图片
3. 返回语音答复时，`agent_session.assets` 中出现真实图片

## 5. 排障建议

如果没有收到图片：

1. 先看服务端是否发出了 `sensor.camera.capture`
2. 再看设备端是否打印了摄像头初始化失败
3. 若设备端已抓拍但服务端超时，优先检查控制连接是否能承载当前图片大小

如果图片已回传但解读不正确：

1. 查看 `result.json` 中的图片路径是否为真实 `.jpg`
2. 查看 `agent_session.capability_traces`
3. 确认最终图片理解是否来自主链路多模态请求
