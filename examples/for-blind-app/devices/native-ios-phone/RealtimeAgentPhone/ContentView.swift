import SwiftUI

/// iOS phone 参考端主页面。
///
/// 主要功能：
/// 1. 展示当前配置、连接状态和最近事件。
/// 2. 提供注册、断开、上传 `sensor.rgb` 测试帧、发送测试 PCM 的操作入口。
/// 3. 作为协议联调面板，不把业务能力写死成 phone/glass 专用 RPC。
struct ContentView: View {
    @EnvironmentObject private var runtime: RealtimeAgentEndpointRuntime

    var body: some View {
        NavigationStack {
            List {
                Section("配置") {
                    labeled("Server", runtime.config.serverURL)
                    labeled("User", runtime.config.userID)
                    labeled("Device", runtime.config.deviceID)
                    labeled("Auth", runtime.config.auth.mode)
                    labeled("Audio input", runtime.config.audioInput.enabled ? "enabled" : "disabled")
                    labeled("Camera", runtime.config.camera.enabled ? "enabled" : "disabled")
                    labeled("Speaker", runtime.config.speaker.enabled ? "enabled" : "disabled")
                }

                Section("状态") {
                    labeled("Control", runtime.controlState)
                    labeled("Stream", runtime.streamState)
                    labeled("Direct camera", runtime.directCameraState)
                    labeled("Speaker bytes", "\(runtime.speakerBytesBuffered)")
                    labeled("RGB uploads", "\(runtime.rgbUploadCount)")
                    labeled("Direct camera frames", "\(runtime.directCameraFrameCount)")
                    labeled("Direct camera bytes", "\(runtime.directCameraBytes)")
                    labeled("Log file", runtime.logFilePath)
                    if !runtime.directCameraSinkURIs.isEmpty {
                        labeled("Direct camera URI", runtime.directCameraSinkURIs.joined(separator: "\n"))
                    }
                }

                Section("操作") {
                    Button("连接并注册") {
                        Task { await runtime.connectAndRegister() }
                    }
                    Button("启动直连相机接收") {
                        runtime.startDirectCameraSink()
                    }
                    Button("上传 sensor.rgb 测试帧") {
                        Task { await runtime.uploadTestRGBFrame(reason: "manual_button") }
                    }
                    Button("上传 sensor.mic 测试 PCM") {
                        Task { await runtime.uploadTestMicPCM() }
                    }
                    Button("停止直连相机接收") {
                        runtime.stopDirectCameraSink()
                    }
                    Button("清空日志") {
                        runtime.clearLogs()
                    }
                    Button("断开连接", role: .destructive) {
                        Task { await runtime.disconnect() }
                    }
                }

                Section("最近事件") {
                    if runtime.eventLog.isEmpty {
                        Text("暂无事件")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(runtime.eventLog, id: \.self) { item in
                            Text(item)
                                .font(.footnote.monospaced())
                                .textSelection(.enabled)
                        }
                    }
                }
            }
            .navigationTitle("RealtimeAgent Phone")
        }
    }

    private func labeled(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.footnote.monospaced())
                .textSelection(.enabled)
        }
    }
}
