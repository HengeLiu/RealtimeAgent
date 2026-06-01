import SwiftUI

/// 播放链路实验 App 入口。
///
/// 主要功能：创建页面状态对象，并展示播放 buffer、AEC 和 cancel 清理实验界面。
@main
struct PlaybackChainExperimentApp: App {
    @StateObject private var model = ExperimentViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
        }
    }
}
