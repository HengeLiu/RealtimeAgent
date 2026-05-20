# python-phone 开发支持组件

`python-phone` 是 RealtimeAgent SDK 的 Python 端开发/测试支持组件，用来在本地模拟手机侧
显示、视觉计算、远程命令和 peer video receiver。它在代码实现和协议交互上会注册为
普通 Device，因此可以真实覆盖设备注册、控制事件、stream 和端侧任务回执；但它不是
SDK 定义的正式手机设备类型，也不要求开发者真实手机端使用 Python 实现。

## 能力边界

Python phone 有两个常用开发支持配置，能力边界不同：

- `phone.mock.yaml`：注册为简单 mock 设备，可声明 `sensor.rgb` 和 `actuator.vibrator`，
  用于协议、资产和设备能力测试。
- `phone.preview.yaml`：当前主要视频/视觉联调入口。它注册为 `device_role=phone`，通过
  `properties.endpoint.role.visual_display`、`properties.endpoint.compute.vision`、
  `properties.actuator.display.rgb` 和 `properties.peer.video.receiver` 声明显示与本地
  视觉计算能力；它不把自己声明成 RGB 传感器。

真实图片、音频和传感器数据只通过 `/ws/stream` 二进制通道传输；控制事件 payload
只携带 `request_id`、`stream_type`、采样策略和状态。

## 视频/视觉模拟组件

Python phone 可以长驻运行成视频/视觉模拟组件，用来显示眼镜端或 browser-glass 上传到同一
`user_id` 设备组内的 `sensor.rgb` 视频流，并运行 YOLOE 找物和红绿灯 YOLO 等本地视觉算法。

设计文档见 [VIDEO_DISPLAY_DESIGN.md](VIDEO_DISPLAY_DESIGN.md)。

本地窗口默认使用 PySide6 实现，OpenCV 只负责 JPEG/PNG 解码和最近帧落盘。启动后
设备不会声明自己是 RGB 传感器，而是通过 `properties.endpoint.role.visual_display`
和 `properties.actuator.display.rgb` 订阅同一用户下的 `sensor.rgb` 输入流。

```bash
uv run --extra gui python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.preview.yaml
```

联调时保持 `browser-glass` 和 `python-phone` 的 `user_id` 一致。普通语音期间
server 的 realtime visual sampler 会向 browser-glass 请求带 `request_id` 的
`sensor.rgb` 单资产帧，这类帧只进入模型/资产链路，不会转发到 phone 窗口。只有普通
连续 RGB stream 或 peer video 任务流会显示在 PySide6 窗口。最近一帧默认写入：

```text
runs/realtime-agent/python-phone/latest-rgb.png
```

## peer video receiver

`phone.preview.yaml` 现在作为 peer video 接收端参与 for-blind-app 找物和红绿灯任务：

1. 注册属性包含 `device_role: phone`、`endpoint.compute.vision: true` 和 `peer.video.receiver: true`。
2. 收到 `peer.video.receiver.start` 后，Python phone 会打开 `ws://<phone-ip>:19081/peer-video/<peer_session_id>`。
3. 端侧通过 `RemoteTaskReporter` 上报 `command.accepted/progress/completed/failed`，包括 `peer.receiver.waiting_vision`、`peer.receiver.ready`、`peer.video.first_frame`、`peer.video.frame_processed` 和 `peer.video.timeout`。
4. Python phone 启动并注册后会后台预热视觉模型；如果任务请求先于模型加载完成到达，会先上报 `peer.receiver.waiting_vision`，server 播报等待，眼镜暂不采集。
5. `peer.receiver.ready` 表示 receiver 和视觉模型都已准备好，server 才会通知眼镜开始连接并采集。
6. 每帧通过 `VisionProcessor` 调用本地视觉识别；`phone.preview.yaml` 默认使用本机 modelscope 目录中的 YOLOE 找物模型和红绿灯 YOLO 模型。
7. `provider: mock` 仍可用于无模型测试；真实模式下模型缺失或依赖缺失会向 server 回报 `command.failed`，不会静默返回 mock。
8. 找物任务稳定命中会完成；超时未命中会返回 `found=false` 并触发 server 侧可播报信号。红绿灯任务在稳定识别绿灯时返回 `can_cross=true` 并触发直接通知。
9. `complete_after_frames` 只用于自动化测试，默认保持 0，由找物稳定命中、绿灯稳定或 30 秒超时结束任务。
10. 退出 phone 端、控制连接断开或窗口关闭时，会停止所有 active receiver 并释放本地端口；sender 未发帧就断开会回报 `command.failed`。

`RemoteTaskReporter` 只是 Python 开发支持组件里的 helper，不是跨语言必需对象。
Swift、JavaScript、Kotlin 或 C 端侧只要发送等价的 `command.*` 控制事件即可。

真实 YOLO 识别依赖是 Python phone 开发支持组件自己的运行依赖，不属于 server SDK 必需依赖。使用 Python phone 跑真实模型前，在端侧 Python 环境中安装：

```bash
uv pip install -r examples/dev-support/devices/python-phone/requirements.vision.txt
```

这个依赖文件包含 YOLOE 文本 prompt 需要的 `clip` 包；不要依赖 Ultralytics 运行时自动安装，否则 `uv` 虚拟环境中可能因为没有 `pip` 而失败。

首次找物还会下载 `mobileclip_blt.ts` 文本编码权重，文件约 572MB。Python phone 会把它放到 `runs/realtime-agent/python-phone/vision-cache/`；真实联调前可以先启动一次 Python phone 让它完成缓存，该文件是端侧模型缓存，不需要提交。

`phone.preview.yaml` 默认 `device: auto`，Python phone 会优先用 CUDA，否则使用 CPU，不会自动使用 macOS MPS。YOLOE / MobileCLIP 路径里可能出现 float64 张量，MPS 不支持这一类型；需要实验 MPS 时再显式改成 `device: mps`。

## 启动

终端 1：

```bash
uv run realtime-agent.server.run --config examples/for-blind-app/audio-server/server.yaml
```

终端 2：

```bash
uv run python -m realtime_agent_python_phone_mock --config examples/dev-support/devices/python-phone/phone.mock.yaml
```

peer video 联调使用 `phone.preview.yaml`，该配置默认以长驻模式运行。`mode: register_only`
只适合自动验收，会在完成注册后退出。
