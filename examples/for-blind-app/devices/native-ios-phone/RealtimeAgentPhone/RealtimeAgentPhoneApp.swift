import SwiftUI

/// realtime-agent iOS phone 参考端入口。
///
/// 主要功能：
/// 1. 加载 `AppConfig.json`。
/// 2. 持有 `RealtimeAgentEndpointRuntime`，供页面触发注册、上传测试图片和消费输出流。
/// 3. 仅实现 event / stream 协议参考，不接管真实录音、播放、AEC 或硬件驱动。
@main
struct RealtimeAgentPhoneApp: App {
    @StateObject private var runtime = RealtimeAgentEndpointRuntime(config: AppConfig.load())

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(runtime)
        }
    }
}
