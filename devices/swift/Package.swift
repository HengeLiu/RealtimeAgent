// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "RealtimeAgentDeviceKit",
    platforms: [
        .macOS(.v13),
        .iOS(.v16),
    ],
    products: [
        .library(name: "RealtimeAgentDeviceKit", targets: ["RealtimeAgentDeviceKit"]),
    ],
    targets: [
        .target(name: "RealtimeAgentDeviceKit"),
        .testTarget(
            name: "RealtimeAgentDeviceKitTests",
            dependencies: ["RealtimeAgentDeviceKit"],
            resources: [
                .copy("Fixtures/rgb-header.json"),
                .copy("Fixtures/rgb-chunk.bin"),
            ]
        ),
    ]
)
