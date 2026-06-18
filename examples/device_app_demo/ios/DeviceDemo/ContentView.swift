import SwiftUI
import UIKit

/// Device Demo 主页面。
///
/// 主要功能：提供“开始音视频对话”的最小交互，并在对话中展示相机回显、音频状态和调试面板。
struct ContentView: View {
    @EnvironmentObject private var runtime: DeviceDemoRuntime
    @State private var showingDebug = false

    var body: some View {
        ZStack {
            if runtime.phase == .conversation {
                ConversationView()
                    .environmentObject(runtime)
                    .transition(.opacity)
            } else {
                StartConversationView()
                    .environmentObject(runtime)
                    .transition(.opacity)
            }
        }
        .overlay(alignment: .topTrailing) {
            DebugInfoButton {
                showingDebug = true
            }
            .padding(.top, 10)
            .padding(.trailing, 18)
            .zIndex(100)
        }
        .animation(.easeInOut(duration: 0.25), value: runtime.phase)
        .sheet(isPresented: $showingDebug) {
            DebugSheet()
                .environmentObject(runtime)
        }
    }
}

private struct DebugInfoButton: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "info")
                .font(.title3.weight(.medium))
                .frame(width: 52, height: 52)
                .background(.thinMaterial, in: Circle())
                .overlay(Circle().stroke(.primary.opacity(0.4), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .contentShape(Circle())
        .accessibilityLabel("打开调试信息")
    }
}

private struct StartConversationView: View {
    @EnvironmentObject private var runtime: DeviceDemoRuntime

    var body: some View {
        VStack {
            Spacer()

            Button {
                Task { await runtime.handlePrimaryButtonTap() }
            } label: {
                ZStack {
                    Circle()
                        .stroke(.primary, lineWidth: 2)
                        .frame(width: 230, height: 230)
                    Text(runtime.primaryButtonTitle)
                        .font(.title2.weight(.medium))
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.primary)
                }
            }
            .buttonStyle(.plain)
            .disabled(!runtime.isPrimaryButtonEnabled)
            .accessibilityLabel("开始音视频对话")

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }

}

private struct ConversationView: View {
    @EnvironmentObject private var runtime: DeviceDemoRuntime

    var body: some View {
        VStack(spacing: 44) {
            CameraPreviewView(session: runtime.cameraPreview.session)
                .frame(maxWidth: 430)
                .aspectRatio(0.72, contentMode: .fit)
                .overlay {
                    if !runtime.cameraPreview.isRunning {
                        Text("摄像头视频回显窗口")
                            .font(.title3.weight(.medium))
                            .foregroundStyle(.blue)
                    }
                }
                .border(.primary, width: 2)
                .padding(.horizontal, 42)
                .padding(.top, 92)

            Spacer()

            AudioConversationBar()
                .padding(.horizontal, 54)
                .padding(.bottom, 56)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }
}

private struct AudioConversationBar: View {
    @EnvironmentObject private var runtime: DeviceDemoRuntime

    var body: some View {
        Button {
            Task { await runtime.stopConversation() }
        } label: {
            TimelineView(.animation) { timeline in
                let phase = timeline.date.timeIntervalSinceReferenceDate
                HStack(spacing: 18) {
                    WaveformView(phase: phase)
                    Text(runtime.conversationStatusText)
                        .font(.title3.weight(.medium))
                        .foregroundStyle(.primary)
                    WaveformView(phase: phase + 0.45)
                }
                .frame(maxWidth: 390, minHeight: 82)
                .padding(.horizontal, 20)
                .overlay(Rectangle().stroke(.primary, lineWidth: 2))
            }
        }
        .buttonStyle(.plain)
        .disabled(!runtime.isStopConversationEnabled)
        .accessibilityLabel("结束音视频对话")
    }
}

private struct WaveformView: View {
    let phase: TimeInterval

    var body: some View {
        HStack(spacing: 3) {
            ForEach(0..<14, id: \.self) { index in
                let value = abs(sin(phase * 5 + Double(index) * 0.55))
                Capsule()
                    .fill(.primary)
                    .frame(width: 3, height: 8 + value * 34)
            }
        }
        .frame(width: 88, height: 46)
    }
}

private struct DebugSheet: View {
    @EnvironmentObject private var runtime: DeviceDemoRuntime
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("连接") {
                    TextField("Server URL", text: $runtime.serverURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    labeled("阶段", runtime.phase.rawValue)
                    labeled("Control", runtime.diagnostics.controlState)
                    labeled("Stream", runtime.diagnostics.streamState)
                    labeled("Registered", runtime.diagnostics.registered ? "true" : "false")
                    labeled("Last event", runtime.diagnostics.lastEventName ?? "-")
                    labeled("Last error", runtime.diagnostics.lastError ?? "-")
                }

                Section("统计") {
                    labeled("Sent events", "\(runtime.diagnostics.sentEvents)")
                    labeled("Received events", "\(runtime.diagnostics.receivedEvents)")
                    labeled("Sent stream chunks", "\(runtime.diagnostics.sentStreamChunks)")
                    labeled("Received output chunks", "\(runtime.diagnostics.receivedOutputChunks)")
                    labeled("Unhandled events", "\(runtime.diagnostics.unhandledEvents)")
                    labeled("Media error", runtime.diagnostics.lastMediaError ?? "-")
                    labeled("Log file", runtime.logFilePath)
                }

                Section("操作") {
                    Button("复制日志") {
                        UIPasteboard.general.string = runtime.logs.reversed().joined(separator: "\n")
                    }
                    .disabled(runtime.logs.isEmpty)

                    Button("清空日志") {
                        runtime.clearLogs()
                    }
                    Button("停止对话", role: .destructive) {
                        Task { await runtime.stopConversation() }
                    }
                    .disabled(!runtime.isStopConversationEnabled)
                }

                Section("日志") {
                    if runtime.logs.isEmpty {
                        Text("暂无日志").foregroundStyle(.secondary)
                    } else {
                        ForEach(runtime.logs, id: \.self) { item in
                            Text(item)
                                .font(.footnote.monospaced())
                                .textSelection(.enabled)
                        }
                    }
                }
            }
            .navigationTitle("调试信息")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("完成") { dismiss() }
                }
            }
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

#Preview {
    ContentView()
        .environmentObject(DeviceDemoRuntime())
}
