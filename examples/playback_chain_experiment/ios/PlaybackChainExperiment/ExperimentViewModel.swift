import AVFoundation
import Foundation
import UIKit

/// 实验日志文件存储。
///
/// 主要功能：异步追加、清空和读取日志文件，避免日志 I/O 阻塞音频采集和播放流程。
final class ExperimentLogStore: @unchecked Sendable {
    let url: URL
    private let queue = DispatchQueue(label: "playback-chain-experiment.log-file")

    init(url: URL) {
        self.url = url
    }

    func append(_ line: String) {
        queue.async { [url] in
            let data = Data((line + "\n").utf8)
            if !FileManager.default.fileExists(atPath: url.path) {
                _ = FileManager.default.createFile(atPath: url.path, contents: nil)
            }
            guard let handle = try? FileHandle(forWritingTo: url) else { return }
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            handle.write(data)
        }
    }

    func clear() {
        queue.async { [url] in
            try? FileManager.default.removeItem(at: url)
        }
    }

    func loadTail(limit: Int) -> [String] {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return text.split(separator: "\n").suffix(limit).map(String.init)
    }
}

/// 播放链路实验页面状态。
///
/// 主要功能：保存服务地址、场景、运行状态、结果路径和日志，并把耗时音频实验放入后台任务。
@MainActor
final class ExperimentViewModel: ObservableObject {
    private static let appBuildMarker = "playback-chain-exp-20260601-vad-upload-wav"
    @Published var audioServerURL: String {
        didSet { UserDefaults.standard.set(audioServerURL, forKey: Self.audioURLKey) }
    }
    @Published var vadURL: String {
        didSet { UserDefaults.standard.set(vadURL, forKey: Self.vadURLKey) }
    }
    @Published var selectedScenario: PlaybackExperimentScenario = .defaultBufferVoiceProcessingOn
    @Published private(set) var isRunning = false
    @Published private(set) var status = "未运行"
    @Published private(set) var route = "-"
    @Published private(set) var wavPath = "-"
    @Published private(set) var vadUploadWAVPath = "-"
    @Published private(set) var timelinePath = "-"
    @Published private(set) var runDirectoryPath = "-"
    @Published private(set) var vadSummary = "-"
    @Published private(set) var logs: [String] = []
    let logFilePath: String

    private let logStore: ExperimentLogStore
    private var task: Task<Void, Never>?
    private var runner: PlaybackExperimentRunner?
    private var wavPlayer: AVAudioPlayer?
    private static let audioURLKey = "PlaybackChainExperiment.audioServerURL"
    private static let vadURLKey = "PlaybackChainExperiment.vadURL"
    private static let defaultAudioURL = "http://192.168.10.10:8778"
    private static let defaultVADURL = "http://192.168.10.10:8777/vad/analyze"

    init() {
        audioServerURL = UserDefaults.standard.string(forKey: Self.audioURLKey) ?? Self.defaultAudioURL
        vadURL = UserDefaults.standard.string(forKey: Self.vadURLKey) ?? Self.defaultVADURL
        let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        let logFileURL = documentsURL.appendingPathComponent("PlaybackChainExperiment.log")
        logStore = ExperimentLogStore(url: logFileURL)
        logFilePath = logFileURL.path
        loadLogsFromDisk()
    }

    /// 启动一次实验。
    ///
    /// 主要逻辑：解析服务地址，创建 runner，在后台执行音频拉取、播放、录音和 VAD 上传。
    /// 返回值：无。
    /// 异常情况：错误会进入页面状态和日志，不抛给 SwiftUI。
    func start() {
        guard !isRunning else {
            appendLog("忽略启动请求：已有实验正在运行")
            return
        }
        guard let audioURL = URL(string: audioServerURL), let vadEndpoint = URL(string: vadURL) else {
            appendLog("服务地址无效 audio=\(audioServerURL) vad=\(vadURL)")
            return
        }
        isRunning = true
        status = "运行中：\(selectedScenario.title)"
        route = "-"
        wavPath = "-"
        vadUploadWAVPath = "-"
        timelinePath = "-"
        runDirectoryPath = "-"
        vadSummary = "-"
        appendLog("开始实验 scenario=\(selectedScenario.rawValue)")
        appendLog("App build marker=\(Self.appBuildMarker)")

        let scenario = selectedScenario
        let runner = PlaybackExperimentRunner()
        self.runner = runner
        task = Task.detached(priority: .userInitiated) { [weak self, runner] in
            do {
	                let result = try await runner.run(
	                    scenario: scenario,
	                    audioServerURL: audioURL,
	                    vadURL: vadEndpoint,
	                    onWAVReady: { wavURL in
	                        Task { @MainActor [weak self] in
	                            self?.wavPath = wavURL.path
	                            self?.appendLog("WAV 已写入，可播放 \(wavURL.path)")
	                            if self?.isRunning == true {
	                                self?.status = "录音已保存"
	                            }
	                        }
	                    }
	                ) { message in
	                    Task { @MainActor [weak self] in
	                        self?.appendLog(message)
                        self?.status = message
                    }
                }
		                await MainActor.run { [weak self] in
		                    self?.isRunning = false
		                    self?.runner = nil
		                    self?.status = result.vadTriggered ? "完成：实时 VAD 已触发" : "完成：录音已保存"
		                    self?.route = result.routeSummary
		                    self?.wavPath = result.wavURL.path
		                    self?.vadUploadWAVPath = result.vadUploadURL?.path ?? "-"
		                    self?.timelinePath = result.timelineURL.path
                    self?.runDirectoryPath = result.runDirectoryURL.path
                    self?.vadSummary = result.vadSummary
                    self?.appendLog("完成 run=\(result.runID)")
                    self?.appendLog("WAV \(result.wavURL.path)")
                    if let vadUploadURL = result.vadUploadURL {
                        self?.appendLog("VADUploadWAV \(vadUploadURL.path)")
                    }
                    self?.appendLog("Timeline \(result.timelineURL.path)")
                    self?.appendLog("VAD \(result.vadSummary)")
                }
            } catch is CancellationError {
                await MainActor.run { [weak self] in
                    self?.isRunning = false
                    self?.runner = nil
                    self?.status = "已停止"
                    self?.appendLog("任务已停止")
                }
            } catch {
                await MainActor.run { [weak self] in
                    self?.isRunning = false
                    self?.runner = nil
                    self?.status = "失败"
                    self?.appendLog("失败：\(error.localizedDescription)")
                }
            }
        }
    }

    /// 请求当前实验执行 cancel。
    func requestCancel() {
        appendLog("手动请求 cancel")
        runner?.requestCancel(reason: "manual_button")
    }

    /// 停止后台任务。
    func stop() {
        appendLog("停止后台任务")
        runner?.requestCancel(reason: "stop_button")
        task?.cancel()
    }

    /// 播放最近一次实验生成的麦克风录音 WAV。
    func playLastWAV() {
        playWAV(path: wavPath, missingMessage: "没有可播放的 WAV")
    }

    /// 播放最近一次实验上传给 VAD 服务的音频 WAV。
    func playLastVADUploadWAV() {
        playWAV(path: vadUploadWAVPath, missingMessage: "没有可播放的 VAD 上传 WAV")
    }

    private func playWAV(path: String, missingMessage: String) {
        guard path != "-" else {
            appendLog(missingMessage)
            return
        }
        let url = URL(fileURLWithPath: path)
        guard FileManager.default.fileExists(atPath: url.path) else {
            appendLog("WAV 文件不存在：\(url.path)")
            return
        }
        do {
            wavPlayer?.stop()
            let player = try AVAudioPlayer(contentsOf: url)
            player.prepareToPlay()
            guard player.play() else {
                appendLog("WAV 播放失败")
                return
            }
            wavPlayer = player
            appendLog("播放 WAV：\(url.lastPathComponent)")
        } catch {
            appendLog("播放 WAV 失败：\(error.localizedDescription)")
        }
    }

    /// 清空页面和文件日志。
    func clearLogs() {
        logs.removeAll()
        logStore.clear()
        appendLog("日志已清空")
    }

    /// 复制全部日志到剪贴板。
    func copyLogs() {
        UIPasteboard.general.string = logs.joined(separator: "\n")
        appendLog("日志已复制")
    }

    /// 复制当前结果摘要到剪贴板。
    func copyResultSummary() {
        UIPasteboard.general.string = [
            "status=\(status)",
            "scenario=\(selectedScenario.rawValue)",
            "audio_server=\(audioServerURL)",
            "vad_url=\(vadURL)",
            "route=\(route)",
            "wav=\(wavPath)",
            "vad_upload_wav=\(vadUploadWAVPath)",
            "timeline=\(timelinePath)",
            "vad=\(vadSummary)",
        ].joined(separator: "\n")
        appendLog("结果摘要已复制")
    }

    private func appendLog(_ message: String) {
        let line = "\(Self.timestamp()) \(message)"
        logs.append(line)
        if logs.count > 500 {
            logs.removeFirst(logs.count - 500)
        }
        logStore.append(line)
    }

    private func loadLogsFromDisk() {
        logs = logStore.loadTail(limit: 500)
    }

    nonisolated private static func timestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss.SSS"
        return formatter.string(from: Date())
    }
}
