# python-phone reference endpoint

`python-phone` 是独立网络端侧参考实现，用来验证手机类设备不依赖固定
`phone` 类型建模，也不通过 `device_id` 点对点收发消息。

## 能力边界

默认注册能力通过 `supports` 表达，server 会据此生成内部路由：

- 生产 `sensor.rgb`；按结构化 supports 声明 `sensor.rgb`，订阅由 server 编译生成
- 消费 `actuator.speaker`、`actuator.haptic`
- 内部路由由 server 根据结构化 `supports` 生成，用于接收 `stream.control.*`、`stream.output.*`
- 通过 `command.*` 事件回报端侧任务 started / progress /
  completed / failed

真实图片、音频和传感器数据只通过 `/ws/stream` 二进制通道传输；控制事件 payload
只携带 `request_id`、`stream_type`、采样策略和状态。

## 手机视觉任务

phone task 不新增 RPC。server 只发布：

```text
command.requested
```

payload 中包含 `task_type`、`task_id`、输入参数和所需 stream。Python phone mock
通过 `handler_packages` 自动发现 handler，执行后上报：

```text
command.accepted
command.progress
command.completed
command.failed
```

当前 SDK 不内置具体业务 handler。找物和红绿灯依赖 YOLO 迁移，先在
`for-blind-app` server 侧保留 mock Task；需要端侧执行时再按业务包注册 handler。

RGB 帧来自 `vision_frames` 配置或默认测试 JPEG，并始终通过 `sensor.rgb` stream
上传。事件日志、任务日志和帧日志会进入运行结果，便于对照真实 iOS 插件行为。

## 视频显示端

Python 手机端可以长驻运行成视频显示端，用来显示眼镜端或浏览器端上传到同一
`user_id` 设备组内的 `sensor.rgb` 视频流，并为后续 YOLO 等本地视觉算法预留扩展点。

设计文档见 [VIDEO_DISPLAY_DESIGN.md](VIDEO_DISPLAY_DESIGN.md)。

本地窗口默认使用 PySide6 实现，OpenCV 只负责 JPEG/PNG 解码和最近帧落盘。启动后
设备不会声明自己是 RGB 传感器，而是通过 `properties.endpoint.role.visual_display`
和 `properties.actuator.display.rgb` 订阅同一用户下的 `sensor.rgb` 输入流。

```bash
uv run --extra gui python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

联调时保持 `browser-glass` 和 `python-phone` 的 `user_id` 一致。浏览器页面连接后，
在“带图输入”区域选择图片并点击“上传所选图片”，图片会通过 server 回显到 PySide6
窗口；未选择图片时浏览器会请求摄像头拍一张。最近一帧默认写入：

```text
runs/audio-chat/python-phone/latest-rgb.png
```

## peer video receiver

`phone.preview.yaml` 现在也可作为 peer video 接收端参与 for-blind-app 找物和红绿灯任务：

1. 注册属性包含 `device_role: phone`、`endpoint.compute.vision: true` 和 `peer.video.receiver: true`。
2. 收到 `peer.video.receiver.start` 后，Python phone 会打开 `ws://<phone-ip>:19081/peer-video/<peer_session_id>`。
3. 端侧通过 `RemoteTaskReporter` 上报 `command.accepted/progress/completed/failed`，包括 `peer.receiver.waiting_vision`、`peer.receiver.ready`、`peer.video.first_frame`、`peer.video.frame_processed` 和 `peer.video.timeout`。
4. Python phone 启动并注册后会后台预热视觉模型；如果任务请求先于模型加载完成到达，会先上报 `peer.receiver.waiting_vision`，server 播报等待，眼镜暂不采集。
5. `peer.receiver.ready` 表示 receiver 和视觉模型都已准备好，server 才会通知眼镜开始连接并采集。
6. 每帧通过 `VisionProcessor` 调用本地视觉识别；`phone.preview.yaml` 默认使用本机 modelscope 目录中的 YOLOE 找物模型和红绿灯 YOLO 模型。
7. `provider: mock` 仍可用于无模型测试；真实模式下模型缺失或依赖缺失会向 server 回报 `command.failed`，不会静默返回 mock。
8. `complete_after_frames` 只用于自动化测试，默认保持 0，由找物稳定命中、绿灯稳定或 30 秒超时结束任务。
9. 退出 phone 端、控制连接断开或窗口关闭时，会停止所有 active receiver 并释放本地端口；sender 未发帧就断开会回报 `command.failed`。

`RemoteTaskReporter` 只是 Python 参考端 helper，不是跨语言必需对象。Swift、JavaScript、Kotlin 或 C 端侧只要发送等价的 `command.*` 控制事件即可。

真实 YOLO 识别依赖是 Python phone 参考端自己的运行依赖，不属于 server SDK 必需依赖。使用 Python phone 跑真实模型前，在端侧 Python 环境中安装：

```bash
uv pip install -r examples/dev-support/devices/python-phone/requirements.vision.txt
```

这个依赖文件包含 YOLOE 文本 prompt 需要的 `clip` 包；不要依赖 Ultralytics 运行时自动安装，否则 `uv` 虚拟环境中可能因为没有 `pip` 而失败。

首次找物还会下载 `mobileclip_blt.ts` 文本编码权重，文件约 572MB。Python phone 会把它放到 `runs/audio-chat/python-phone/vision-cache/`；真实联调前可以先启动一次 Python phone 让它完成缓存，该文件是端侧模型缓存，不需要提交。

`phone.preview.yaml` 默认 `device: auto`，Python phone 会优先用 CUDA，否则使用 CPU，不会自动使用 macOS MPS。YOLOE / MobileCLIP 路径里可能出现 float64 张量，MPS 不支持这一类型；需要实验 MPS 时再显式改成 `device: mps`。

## 启动

终端 1：

```bash
uv run audio-chat.server.run --config examples/for-blind-app/audio-server/server.yaml
```

终端 2：

```bash
uv run python -m audio_chat_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

peer video 联调使用 `phone.preview.yaml`，该配置默认以长驻模式运行。`mode: register_only`
只适合自动验收，会在完成注册后退出。
