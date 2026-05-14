// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "AudioChatDeviceKit",
    platforms: [
        .macOS(.v13),
        .iOS(.v16),
    ],
    products: [
        .library(name: "AudioChatDeviceKit", targets: ["AudioChatDeviceKit"]),
    ],
    targets: [
        .target(name: "AudioChatDeviceKit"),
        .testTarget(
            name: "AudioChatDeviceKitTests",
            dependencies: ["AudioChatDeviceKit"],
            resources: [
                .copy("Fixtures/rgb-header.json"),
                .copy("Fixtures/rgb-chunk.bin"),
            ]
        ),
    ]
)
