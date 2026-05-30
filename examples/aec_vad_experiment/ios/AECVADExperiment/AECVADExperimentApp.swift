import SwiftUI

@main
struct AECVADExperimentApp: App {
    @StateObject private var model = ExperimentViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
        }
    }
}
