import SwiftUI

/// 盲人业务手机端 App 入口。
///
/// 主要功能：
/// 1. 复用 SDK iOS 运行时的 `ContentView`、控制连接和视频接收能力。
/// 2. 在 App 启动时显式注册业务手机任务插件。
/// 3. 避免业务插件依赖未引用全局变量的隐式初始化顺序。
@main
struct BusinessGlassesVideoReceiverApp: App {
    init() {
        FindObjectPhoneCapabilityInstaller.install()
        TrafficLightPhoneCapabilityInstaller.install()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
