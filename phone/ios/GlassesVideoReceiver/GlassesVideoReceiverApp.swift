import SwiftUI

/// iOS 视频回显验证应用入口。
///
/// 主要功能：
/// 1. 启动最小视频回显页面。
/// 2. 负责创建页面级状态与接收服务。
/// 3. 作为第三阶段手机原生验证载体。
@main
struct GlassesVideoReceiverApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
