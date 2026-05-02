# find_object

本目录实现盲人 AI 眼镜的找物体能力，是当前业务工程对 SDK 的主要验证样例。

| 子目录 | 职责 |
| --- | --- |
| [server](./server) | 服务端 Tool 和 Task。 |
| [phone](./phone) | 手机侧处理器、手机任务，以及 iOS 能力插件源码。 |

## 当前手机端检测方式

真实 iOS 插件 [phone/ios/FindObjectPhoneCapability.swift](./phone/ios/FindObjectPhoneCapability.swift) 已按业务层边界接入 CoreML YOLO 检测器：

1. App 资源中存在 `FindObjectYOLO.mlmodelc` 或 Tool 下发的 `model_name.mlmodelc` 时，手机端使用 Vision + CoreML 执行目标检测。
2. 无模型资源时，自动回退启发式检测，只用于验证注册、视频帧接收、手机任务和事件回流链路。
3. 检测结果会上报 `source`、`label`、`bbox`、`confidence`、`position` 和 `summary`，服务端 `find_object_task` 根据 `found=true` 完成任务并释放手机任务与视频链路。

真实产品验证前应把转换好的 YOLO CoreML 模型加入业务 iOS 工程资源，并通过真机确认 `source=coreml_yolo`。
