import SwiftUI

/// 播放链路实验主界面。
///
/// 主要功能：配置音频分片服务和 VAD 服务，运行不同 buffer/AEC/cancel 场景，并提供日志复制入口。
struct ContentView: View {
    @EnvironmentObject private var model: ExperimentViewModel

    var body: some View {
        NavigationStack {
            List {
                Section("服务") {
                    TextField("Audio Server URL", text: $model.audioServerURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("VAD URL", text: $model.vadURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                }

                Section("场景") {
                    Picker("实验场景", selection: $model.selectedScenario) {
                        ForEach(PlaybackExperimentScenario.allCases) { scenario in
                            Text(scenario.title).tag(scenario)
                        }
                    }
                    .disabled(model.isRunning)

                    Text(model.selectedScenario.description)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("运行") {
                    Button("开始实验") {
                        model.start()
                    }
                    .disabled(model.isRunning)

                    Button("模拟 Cancel") {
                        model.requestCancel()
                    }
                    .disabled(!model.isRunning)

                    Button("停止任务") {
                        model.stop()
                    }
                    .disabled(!model.isRunning)
                }

                Section("结果") {
                    labeled("状态", model.status)
                    labeled("日志文件", model.logFilePath)
                    labeled("运行目录", model.runDirectoryPath)
                    labeled("路由", model.route)
                    labeled("WAV", model.wavPath)
                    labeled("Timeline", model.timelinePath)
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

                    Button("复制结果摘要") {
                        model.copyResultSummary()
                    }
                    .disabled(model.logs.isEmpty)

                    Button("清空日志") {
                        model.clearLogs()
                    }
                    .disabled(model.isRunning)
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
            .navigationTitle("播放链路实验")
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
