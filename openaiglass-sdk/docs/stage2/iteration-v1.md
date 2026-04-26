# SDK v1 迭代记录

本文记录 SDK 团队根据业务能力开发反馈进行的第一轮优化。

## 1. 输入反馈

业务团队在 `openaiglass-for-blind/docs/stage1/develop/架构阻塞点说明与改进建议.md` 中反馈：

1. iOS 通用运行时已有 `PhoneTaskCapabilityRuntime` 和 `PhoneCapabilityBootstrap`，但只能注册单个 runtime。
2. 多个 iOS 业务插件同时加入 App target 时，后注册的能力会覆盖先注册的能力。
3. 业务侧如果自行写组合 Runtime，会把 SDK 应负责的多能力分发逻辑扩散到业务工程。
4. iOS 插件接入 target 的方式需要在文档中明确。

## 2. 本轮 SDK 改动

### 2.1 iOS 多能力注册表

新增 `PhoneTaskCapabilityRegistry`：

```swift
PhoneTaskCapabilityRegistry.register(taskType: "demo_phone_task") {
    DemoPhoneCapabilityRuntime()
}
```

它按 `taskType` 保存业务能力运行时工厂。`CameraStreamStore` 仍只持有一个 `PhoneTaskCapabilityRuntime`，但默认工厂会优先创建组合运行时，由组合运行时完成多能力分发。

### 2.2 组合运行时

新增 `RegisteredPhoneTaskCapabilityRuntime`，负责：

1. `startTask` 时按 `taskType` 创建业务运行时。
2. 记录 `taskID -> runtime`，确保 `stopTask` 回到同一个实例。
3. 将视频帧投递给当前活跃任务对应的业务运行时。
4. 未知 `taskType` 使用 `NoopPhoneTaskCapabilityRuntime` 兜底。

### 2.3 旧接口兼容

保留 `PhoneCapabilityRuntimeFactory.register { ... }`，但它只作为旧式单能力入口。新业务插件必须使用 `PhoneTaskCapabilityRegistry.register(taskType:runtimeBuilder:)`，否则无法稳定表达多个能力的分发关系。

## 3. 本轮不进入 SDK 的内容

Swift Package、XCFramework 和独立插件包发布形态暂不在本轮实现。

原因：

1. 当前 iOS SDK 仍以可直接打开的 Xcode 工程交付。
2. 业务团队下一轮迭代只需要稳定把多个 Swift 插件加入同一个 App target。
3. 包发布会引入版本号、资源拷贝、target 依赖和二进制兼容问题，应在 SDK 形态进一步稳定后单独处理。

本轮文档给出的临时约定是：业务 Swift 插件继续放在 `openaiglass-for-blind/capabilities/<capability>/phone/ios/`，由手机宿主 App target 显式加入 Compile Sources，并在启动入口集中调用各插件 `install()`。

## 4. 文档同步

已同步更新：

1. `openaiglass-for-blind/SDK安装与能力开发指南.md`
2. `openaiglass-sdk/phone-ios/README.md`
3. `openaiglass-sdk/docs/structure-design/手机SDK运行时设计.md`

## 5. 验证范围

本轮新增 iOS 测试覆盖：

1. 多个 `taskType` 同时注册后不会互相覆盖。
2. 停止某个 `taskID` 时只停止对应业务运行时。
3. 视频帧只投递给当前活跃手机任务。

真机联调仍需业务团队在下一轮把插件注册方式切换到 `PhoneTaskCapabilityRegistry` 后验证。
