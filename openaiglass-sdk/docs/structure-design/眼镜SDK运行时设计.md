# 眼镜SDK运行时设计

## 1. 文档定位

本文档用于明确眼镜端 SDK 运行时的职责边界。

它服务于以下目标：

1. 让眼镜端从具体业务设备代码收敛成通用感知与执行 SDK运行时。
2. 让业务能力通过服务端任务和手机端处理器扩展，而不是写死在眼镜固件中。
3. 让眼镜端只关心采集、播放、控制消息和媒体链路，不关心完整业务编排。

本文档不要求第二期完成正式 ESP32 SDK 发布，也不要求一次性移动 `openaiglass-sdk/glass-esp32` 到 `glass/esp32`。

## 2. 核心结论

眼镜端在 SDK 体系中是**感知与执行侧 SDK运行时**。

眼镜 SDK运行时只负责：

1. 连接服务端。
2. 完成设备注册和心跳。
3. 采集音频、图片、视频和基础传感器数据。
4. 按控制消息启动或停止媒体流。
5. 播放服务端下发的音频或通知。
6. 执行低层设备动作，例如震动或提示音。

眼镜 SDK运行时不负责：

1. 完整业务任务编排。
2. Agent 上下文维护。
3. 手机任务调度。
4. 复杂视觉算法。
5. 设备组绑定决策。
6. 地图、导航、找物体等业务能力判断。

当前仓库中，`openaiglass-sdk/glass-esp32` 仍是实际眼镜工程；统一启动入口是 `openaiglass glass firmware --repo-root .`。业务侧 `scripts/run_glass.sh` 只保留为兼容旧习惯的薄包装，负责传入盲人业务默认配置路径。

## 3. 当前代码落点

当前相关代码：

1. `openaiglass-sdk/glass-esp32/main/glass_main.c`
2. `openaiglass-sdk/glass-esp32/main/test_wakenet.c`
3. `openaiglass-for-blind/host/glass/config/local_build.env.openaiglass-for-blind`
4. `openaiglasses.cli.glass`
5. `glass/README.md`

当前相关设计文档：

1. [设备握手与注册协议设计.md](./设备握手与注册协议设计.md)
2. [统一通信协议信封设计.md](./统一通信协议信封设计.md)
3. [媒体流传输格式设计.md](./媒体流传输格式设计.md)

## 4. 运行时职责

### 4.1 注册与心跳

眼镜 SDK运行时启动后，应完成：

1. 连接 Wi-Fi。
2. 建立服务端控制连接。
3. 发送 `device.register`。
4. 等待 `device.registered`。
5. 按服务端要求发送 `device.heartbeat`。

注册 payload 至少包含：

```json
{
  "device_id": "glass-001",
  "device_type": "glass",
  "firmware_version": "0.1.0",
  "auth": {
    "mode": "pair_token",
    "pair_token": "pair_xxx"
  }
}
```

眼镜 SDK运行时不应自行处理：

1. 手机绑定选择。
2. 设备组查询。
3. 任务创建。
4. Agent 会话状态。

### 4.2 音频采集与播放

眼镜 SDK运行时负责音频设备层动作：

1. WakeNet 或按钮触发后的音频采集。
2. 音频上行流连接。
3. 服务端 TTS 音频下行播放。
4. 播放期间暂停麦克风上行，避免串音。
5. 播放完成后恢复待命监听。

眼镜 SDK运行时不负责：

1. ASR 调用。
2. 模型回复生成。
3. Agent 多轮上下文维护。
4. 任务事件是否需要回流 Agent。

### 4.3 图片与视频采集

眼镜 SDK运行时负责按控制消息执行采集：

1. 单次抓拍。
2. 视频流启动。
3. 视频流停止。
4. 媒体帧编码和上行。

眼镜 SDK运行时不应判断：

1. 当前是否找到目标物体。
2. 当前是否可以过马路。
3. 当前导航是否偏航。

这些判断应由手机端处理器或服务端任务完成。

### 4.4 控制消息处理

眼镜 SDK运行时必须理解公共控制消息信封。

第二期至少需要支持：

1. `device.registered`
2. `device.register.failed`
3. `device.binded`
4. `voice.session.open`
5. `voice.session.close`
6. `sensor.camera.capture`
7. `sensor.camera.stream.start`
8. `sensor.camera.stream.stop`
9. `actuator.audio.play`

规则：

1. 眼镜 SDK运行时只执行设备动作。
2. 控制消息中的业务原因字段只用于日志和调试。
3. 眼镜 SDK运行时不应根据业务原因切换不同业务流程。

## 5. 能力接入边界

### 5.1 SDK运行时负责

1. Wi-Fi 和服务端连接。
2. 注册、心跳和重连。
3. 音频采集与播放。
4. 图片和视频采集。
5. 媒体帧发送。
6. 基础设备动作。

### 5.2 SDK / 服务端负责

1. 设备组绑定。
2. 任务创建和状态推进。
3. 手机任务调度。
4. 通知协调。
5. Agent 上下文。

### 5.3 手机端负责

1. 接收眼镜视频流。
2. 执行本地视觉或传感器辅助判断。
3. 上报结构化任务事件。

## 6. 与 SDK 公共契约的关系

眼镜 SDK运行时必须依赖以下公共契约：

1. `ControlMessage`
2. `device.register`
3. `device.registered`
4. `device.binded`
5. `device.heartbeat`
6. `sensor.camera.capture`
7. `sensor.camera.stream.start`
8. `sensor.camera.stream.stop`
9. `actuator.audio.play`

眼镜 SDK运行时不应依赖：

1. 服务端任务运行时内部结构。
2. 手机任务内部状态。
3. 官方 openaiglass-for-blind 的业务类名。
4. `ScenarioRunner` 的回放实现。

## 7. 第二期验收标准

第二期眼镜 SDK运行时收口完成时，应满足：

1. `openaiglass-sdk/glass-esp32` 不保留 `find_object / navigation / map / timer` 这类业务分支。
2. 新增业务能力时，不需要修改眼镜端底层采集、播放和连接逻辑。
3. 眼镜端所有跨端动作都通过公共控制消息或媒体流协议触发。
4. 眼镜端调试日志能看到 `device_id / session_id / task_id / stream_id` 等关键关联字段。
5. 真机联调失败时，可以区分是注册、控制消息、媒体链路还是业务能力问题。
6. `openaiglass.sdk.preflight` 中的 `sdk_boundary` 检查必须通过，防止眼镜端重新出现具体业务能力分支。

## 8. 后续工作

后续建议继续做：

1. 检查 `openaiglass-sdk/glass-esp32` 中是否还有业务能力词汇。
2. 梳理眼镜端支持的控制消息清单。
3. 将眼镜端启动、注册、媒体链路和播放链路纳入联调检查文档。
4. 后续目录迁移时，再考虑把 `openaiglass-sdk/glass-esp32` 移入 `glass/esp32`。
