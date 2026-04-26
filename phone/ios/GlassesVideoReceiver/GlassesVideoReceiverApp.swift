import SwiftUI

/// iOS 手机 SDK运行时 应用入口。
///
/// 主要功能：
/// 1. 启动最小运行时页面。
/// 2. 负责创建页面级状态与接收服务。
/// 3. 作为手机侧通用 SDK运行时 载体。
@main
struct GlassesVideoReceiverApp: App {
    init() {
        PhoneCapabilityBootstrap.applyRegisteredInstallers()
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
