# iteration-v13：真 iOS 手机视觉资源管理

## 本轮目标

补齐真 iOS 手机视觉执行框架的第一版资源管理能力，让 Swift 业务插件通过 `vision_policy` 声明资源需求，由 SDK 通用运行时统一处理帧率、最大帧数、独占模型资源、抢占、功耗降级和资源事件回流。

## 主要改动

1. 新增 iOS `VisionResourceCoordinator`，支持 `VisionTaskPolicy`、`VisionTaskLease`、`VisionResourceEvent` 和帧投递决策。
2. `RegisteredPhoneTaskCapabilityRuntime` 在启动任务时申请视觉资源租约，资源不足时拒绝任务，高优先级任务可抢占低优先级任务。
3. 视频帧进入业务插件前先经过资源协调器，支持 `min_frame_interval_ms`、`max_frames` 和过载事件。
4. `CameraStreamStore` 记录视觉资源事件，并在有 `phoneDeviceID` 时通过 `PhoneTaskEventReportAPI` 异步回流服务端任务事件。
5. iOS 包清单加入 `VisionResourceCoordinator.swift`，package-check 可校验新增运行时代码。

## 当前边界

1. 本轮不内置具体视觉模型，也不实现 YOLO、盲道、红绿灯或找物算法。
2. 功耗治理第一版读取任务参数中的 `power_mode`，尚未自动接入真实电量和热状态采样。
3. SQLite 任务持久化、完整播放仲裁、账号权限和远程配置中心仍是后续优先项。

## 验证结果

已通过：

```bash
xcodebuild test -project openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj -scheme GlassesVideoReceiver -destination 'platform=iOS Simulator,name=iPhone 17'
```

第一次使用计划文档中的 `iPhone 16` 目的地执行失败，原因是本机没有该模拟器；改用本机可用的 `iPhone 17` 后测试通过。
