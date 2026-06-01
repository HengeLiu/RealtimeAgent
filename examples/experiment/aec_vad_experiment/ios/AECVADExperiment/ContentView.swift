import SwiftUI

/// AEC/VAD 实验主界面。
///
/// 主要功能：输入 VAD 地址，分别运行 Voice Processing 开/关实验，并展示 WAV、VAD 和日志结果。
struct ContentView: View {
    @EnvironmentObject private var model: ExperimentViewModel

    var body: some View {
        NavigationStack {
            List {
                Section("VAD 服务") {
                    TextField("VAD URL", text: $model.vadURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                }

                Section("运行") {
                    Button("VoiceProcessing 开") {
                        model.start(voiceProcessingEnabled: true)
                    }
                    .disabled(model.isRunning)

                    Button("VoiceProcessing 关") {
                        model.start(voiceProcessingEnabled: false)
                    }
                    .disabled(model.isRunning)

                    Button("取消") {
                        model.cancel()
                    }
                    .disabled(!model.isRunning)
                }

                Section("单步定位") {
                    ForEach(AudioProbeStep.allCases) { step in
                        Button(step.title) {
                            model.runProbeStep(step)
                        }
                        .disabled(model.isRunning)
                    }
                }

                Section("结果") {
                    labeled("状态", model.status)
                    labeled("日志文件", model.logFilePath)
                    labeled("路由", model.route)
                    labeled("WAV", model.wavPath)
                    labeled("VAD", model.vadSummary)
                }

                Section("日志操作") {
                    Button("播放录音 WAV") {
                        model.playLastWAV()
                    }
                    .disabled(model.wavPath == "-" || model.isRunning)

                    Button("复制日志") {
                        model.copyLogs()
                    }
                    .disabled(model.logs.isEmpty)

                    Button("清空日志") {
                        model.clearLogs()
                    }
                }

                Section("日志") {
                    if model.logs.isEmpty {
                        Text("暂无日志")
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(model.logs.reversed(), id: \.self) { line in
                            Text(line)
                                .font(.footnote.monospaced())
                                .textSelection(.enabled)
                        }
                    }
                }
            }
            .navigationTitle("AEC/VAD 实验")
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
        .environmentObject(ExperimentViewModel())
}
