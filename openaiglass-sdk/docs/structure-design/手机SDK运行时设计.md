# 手机SDK运行时设计

## 1. 文档定位

本文档用于明确第二期下半程中手机端 SDK运行时 的职责边界。

它服务于以下目标：

1. 让 `openaiglass-sdk/phone-ios` 从具体业务 App 收敛成通用手机 SDK运行时。
2. 让业务能力放在 `openaiglass-for-blind/` 或外部开发者项目中，而不是继续侵入根目录手机工程。
3. 让手机端开发者只实现本地处理器、手机任务或传感器提供者，不关心设备组绑定和服务端路由。

本文档不定义完整 iOS SDK 发布形态，也不要求第二期完成 Android 侧 SDK运行时。

## 2. 核心结论

手机端在 SDK 体系中是**边缘计算侧 SDK运行时**。

手机 SDK运行时只负责：

1. 设备注册与心跳。
2. 接收服务端控制消息。
3. 接收眼镜视频流。
4. 承载手机侧任务。
5. 调用已注册的手机侧能力。
6. 把结构化事件上报服务端。
7. 暴露最小调试状态，便于联调。

手机 SDK运行时不负责：

1. 定义具体业务能力。
2. 维护完整 Agent 上下文。
3. 自行决定服务端任务最终状态。
4. 直接处理设备组绑定规则。
5. 直接拼装非公共协议字段。

当前仓库中，根目录 `openaiglass-sdk/phone-ios/GlassesVideoReceiver` 应被视为通用手机 SDK运行时；`capabilities/find_object/phone/ios/FindObjectPhoneCapability.swift` 才是官方样例业务能力。

## 3. 当前代码落点

当前相关代码：

1. `openaiglass-sdk/phone-ios/GlassesVideoReceiver/Networking/CameraSinkServer.swift`
2. `openaiglass-sdk/phone-ios/GlassesVideoReceiver/Networking/PhoneTaskEventReportAPI.swift`
3. `openaiglass-sdk/phone-ios/GlassesVideoReceiver/Core/CameraStreamStore.swift`
4. `openaiglass-sdk/phone-ios/GlassesVideoReceiver/Core/MediaFrameDecoder.swift`
5. `capabilities/find_object/phone/ios/FindObjectPhoneCapability.swift`
6. `capabilities/find_object/phone/ios/FindObjectPhoneCapabilityTests.swift`

当前已经形成的边界：

1. 根目录手机工程负责注册、控制消息、视频接收和通用任务承载。
2. `find_object` 业务检测逻辑已迁到 `capabilities/find_object/phone/ios`。
3. 手机侧任务事件通过 `/api/tasks/report-event` 回传服务端。
4. 根目录 Xcode 工程默认不再编译 `FindObjectPhoneCapability.swift`，官方样例能力必须由示例宿主或外部 App target 显式接入。

## 4. 运行时职责

### 4.1 注册与绑定接入

手机 SDK运行时启动后，应完成：

1. 读取本地配置。
2. 建立服务端控制连接。
3. 发送 `device.register`。
4. 等待 `device.registered`。
5. 等待或接收设备组绑定状态。

手机 SDK运行时只需要上报：

1. `device_id`
2. `device_type=phone`
3. `camera_sink_ws_uri`
4. `desired_glass_device_id`
5. `auth`

不应由手机 SDK运行时自行维护：

1. 眼镜与手机绑定表。
2. 设备组生命周期。
3. 任务全局状态。

### 4.2 视频接收

手机 SDK运行时负责接收眼镜视频流。

职责包括：

1. 启动本地视频接收服务。
2. 接收 WebSocket 视频帧。
3. 解码媒体帧。
4. 将帧投递给当前运行中的手机任务。
5. 维护最近帧状态，便于 UI 和调试。

不属于 SDK运行时 的职责：

1. 判断当前帧里是否有水杯、红绿灯或障碍物。
2. 把某个业务算法写死到视频接收链路。
3. 因某个业务命中直接修改服务端任务状态。

### 4.3 手机任务承载

手机 SDK运行时必须识别以下公共控制消息：

1. `sdk.phone.task.start`
2. `sdk.phone.task.stop`

`sdk.phone.task.start` 的最小 payload：

```json
{
  "task_id": "task_xxx",
  "task_type": "find_object_phone_task",
  "stream_id": "stream_xxx",
  "glass_device_id": "glass-001",
  "params": {}
}
```

手机 SDK运行时收到启动命令后，只做通用动作：

1. 创建本地任务记录。
2. 保存 `task_id / task_type / stream_id / glass_device_id / params`。
3. 按 `task_type` 查找已注册的手机能力。
4. 将后续视频帧投递给该能力。

SDK运行时不应对某个具体 `task_type` 写业务分支。第二期最终验收要求是：根目录 `openaiglass-sdk/phone-ios` 可以接收任意 `task_type` 并交给已注册能力处理，但自身不包含 `find_object`、导航、地图或计时器等业务分支。

### 4.4 事件上报

手机 SDK运行时通过统一 HTTP 入口上报任务事件：

```text
POST /api/tasks/report-event
```

请求体：

```json
{
  "task_id": "task_xxx",
  "phone_device_id": "phone-001",
  "event_name": "phone.vision.find_object.result",
  "payload": {}
}
```

规则：

1. 手机 SDK运行时只上报事实事件。
2. 服务端 `BaseTask` 决定任务是否完成、失败或继续运行。
3. `payload` 可以扩展，但不能把路由和设备组绑定细节塞入业务字段。

## 5. 能力接入边界

### 5.1 SDK运行时负责

1. 控制连接。
2. 心跳。
3. 视频接收。
4. 本地任务生命周期容器。
5. 事件上报。
6. 最小运行状态展示。

### 5.2 能力负责

1. 解释 `params` 中的业务参数。
2. 处理视频帧或传感器数据。
3. 产出结构化结果。
4. 决定上报什么事件。

### 5.3 服务端负责

1. 设备组绑定。
2. 任务创建和取消。
3. 全局任务状态推进。
4. 通知协调。
5. Agent 上下文维护。

## 6. 与 SDK 公共契约的关系

手机 SDK运行时必须依赖以下公共契约：

1. `ControlMessage`
2. `device.register`
3. `device.registered`
4. `device.binded`
5. `sdk.phone.task.start`
6. `sdk.phone.task.stop`
7. `/api/tasks/report-event`

手机 SDK运行时不应依赖：

1. 服务端私有连接表。
2. `ControlRuntime` 私有字段。
3. `ScenarioRunner` 内部实现。
4. 某个官方 openaiglass-for-blind 的内部类名。

## 7. 第二期验收标准

第二期手机 SDK运行时收口完成时，应满足：

1. 根目录 `openaiglass-sdk/phone-ios` 只保留 SDK运行时 逻辑。
2. 具体业务能力位于 `capabilities/find_object/phone/ios` 或外部开发者项目。
3. 新增业务能力时，不需要修改视频接收和控制连接底层逻辑。
4. 手机任务启动、停止和事件上报都符合 [SDK公共契约设计.md](/Users/elio/dev/llm-project/OpenAIglassesDemo_2/doc/structure-design/SDK公共契约设计.md)。
5. 根目录手机测试只验证 SDK运行时 通用行为，不验证某个具体业务算法。
6. `script/run_sdk_preflight.py` 中的 `sdk_boundary` 检查必须通过，防止官方样例能力重新侵入根目录手机运行时。

## 8. 后续工作

第二期下半程建议继续做：

1. 检查 `openaiglass-sdk/phone-ios` 中是否还有具体业务词汇。
2. 将残留业务分支迁回 `capabilities/find_object/phone/ios`。
3. 为 `sdk.phone.task.start / stop` 增加金样测试。
4. 为 `/api/tasks/report-event` 增加契约测试。
