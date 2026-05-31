import SwiftUI

@main
struct DeviceDemoApp: App {
    @StateObject private var runtime = DeviceDemoRuntime()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(runtime)
        }
    }
}
