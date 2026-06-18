import SwiftUI

@main
struct DeviceDemoApp: App {
    @StateObject private var runtime = DeviceDemoRuntime()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(runtime)
        }
        .onChange(of: scenePhase) { newPhase in
            if newPhase == .background {
                runtime.handleEnteredBackground()
            }
        }
    }
}
