# 手机端示例

手机端不是 Python SDK 代码。本目录用于放置手机平台工程和启动脚本。

当前阶段 `run.sh` 委托根目录 `phone/ios` 中的通用手机 SDK运行时 工程启动。

业务能力代码不放在根目录手机 SDK运行时 中，而是放在：

1. `example/phone/ios/FindObjectPhoneCapability.swift`
2. `example/phone/ios/FindObjectPhoneCapabilityTests.swift`

根目录 `phone/ios` 只负责设备注册、控制消息、视频接收和通用 `PhoneTaskCapabilityRuntime` 承载。

当前装配方式：

1. 根 SDK运行时 通过 `PhoneCapabilityRuntimeFactory` 创建手机能力运行时。
2. 根目录 `phone/ios` 默认只编译通用 SDK运行时，不再默认编译任何官方样例能力。
3. `example/phone/ios/FindObjectPhoneCapability.swift` 作为外部开发者项目中的能力插件源文件存在，展示如何向 `PhoneCapabilityBootstrap` 注册安装函数。
4. 真机启用该样例时，应由示例宿主或外部 App target 显式加入该能力源文件；SDK运行时 本身不认识 `find_object`。

这样根 SDK运行时 不再直接调用、默认编译或默认识别某个具体业务能力，而是只承载通用装配入口。
