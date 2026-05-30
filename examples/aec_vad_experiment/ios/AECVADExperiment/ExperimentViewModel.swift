import AVFoundation
import Foundation
import UIKit

/// 实验日志文件存储。
///
/// 主要功能：在非主线程直接追加、清空和读取日志文件，用于定位 UI 卡死前最后执行的音频 API。
final class ExperimentLogStore: @unchecked Sendable {
    let url: URL
    private let queue = DispatchQueue(label: "aec-vad-experiment.log-file")

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
        return text
            .split(separator: "\n")
            .suffix(limit)
            .map(String.init)
    }
}

/// AEC/VAD 实验页面状态。
///
/// 主要功能：保存按钮状态、实验结果和日志，并把耗时实验放到后台任务中执行。
@MainActor
final class ExperimentViewModel: ObservableObject {
    @Published var vadURL: String {
        didSet { UserDefaults.standard.set(vadURL, forKey: Self.vadURLKey) }
    }
    @Published private(set) var isRunning = false
    @Published private(set) var status = "未运行"
    @Published private(set) var route = "-"
    @Published private(set) var wavPath = "-"
    @Published private(set) var vadSummary = "-"
    @Published private(set) var logs: [String] = []
    let logFilePath: String

    private let experimentQueue = DispatchQueue(label: "aec-vad-experiment.audio", qos: .userInitiated)
    private let probe = AudioStepProbe()
    private let logStore: ExperimentLogStore
    private var wavPlayer: AVAudioPlayer?
    private static let vadURLKey = "AECVADExperiment.vadURL"
    private static let defaultVADURL = "http://192.168.10.10:8777/vad/analyze"

    init() {
        vadURL = UserDefaults.standard.string(forKey: Self.vadURLKey) ?? Self.defaultVADURL
        let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        let logFileURL = documentsURL.appendingPathComponent("AECVADExperiment.log")
        self.logStore = ExperimentLogStore(url: logFileURL)
        self.logFilePath = logFileURL.path
        loadLogsFromDisk()
    }

    /// 启动一次实验。
    ///
    /// 主要逻辑：在后台创建独立 runner，主线程只负责接收进度和刷新界面。
    /// 参数：`voiceProcessingEnabled` 表示是否启用系统 Voice Processing。
    /// 返回值：无。
    /// 异常情况：实验失败时写入状态和日志，不抛给 UI。
    func start(voiceProcessingEnabled: Bool) {
        guard !isRunning else {
            appendLog("忽略启动请求：已有实验正在运行")
            return
        }
        isRunning = true
        status = voiceProcessingEnabled ? "运行中：VoiceProcessing 开" : "运行中：VoiceProcessing 关"
        vadSummary = "-"
        route = "-"
        wavPath = "-"
        appendLog("开始实验 voice_processing=\(voiceProcessingEnabled)")

        let endpoint = URL(string: vadURL)
        let logStore = self.logStore
        experimentQueue.async { [weak self] in
            let runner = AECExperimentRunner()
            do {
                let result = try runner.run(voiceProcessingEnabled: voiceProcessingEnabled, vadURL: endpoint) { [weak self] message in
                    logStore.append("\(Self.timestamp()) 完整实验进度 \(message)")
                    DispatchQueue.main.async {
                        self?.status = message
                        self?.appendLog(message)
                    }
                }
                DispatchQueue.main.async { [weak self] in
                    self?.isRunning = false
                    self?.status = result.vadTriggered == true ? "完成：VAD 已触发" : "完成：VAD 未触发"
                    self?.route = result.routeSummary
                    self?.wavPath = result.wavURL.path
                    self?.vadSummary = result.vadSummary
                    self?.appendLog("完成 wav=\(result.wavURL.path)")
                    self?.appendLog("VAD \(result.vadSummary)")
                    self?.appendLog("路由 \(result.routeSummary)")
                }
            } catch {
                DispatchQueue.main.async { [weak self] in
                    self?.isRunning = false
                    self?.status = "失败"
                    self?.appendLog("失败：\(error.localizedDescription)")
                }
            }
        }
    }

    /// 执行单步音频探针。
    ///
    /// 主要逻辑：每一步前后都先写入持久日志；如果某个系统音频 API 卡死，重启后可根据最后一行定位。
    /// 参数：`step` 是要执行的单步操作。
    /// 返回值：无。
    /// 异常情况：错误会写入日志和状态。
    func runProbeStep(_ step: AudioProbeStep) {
        guard !isRunning else {
            appendLog("忽略单步请求：已有实验正在运行")
            return
        }
        isRunning = true
        status = "单步：\(step.title)"
        appendLog("单步排查 BEGIN \(step.title)")
        let probe = self.probe
        let logStore = self.logStore
        experimentQueue.async { [weak self, probe, logStore] in
            logStore.append("\(Self.timestamp()) 后台开始 \(step.title)")
            do {
                let message = try probe.run(step)
                logStore.append("\(Self.timestamp()) 后台完成 \(step.title): \(message)")
                DispatchQueue.main.async { [weak self] in
                    self?.isRunning = false
                    self?.status = "单步完成：\(step.title)"
                    self?.route = message
                    self?.appendLog("单步排查 END \(step.title): \(message)")
                }
            } catch {
                logStore.append("\(Self.timestamp()) 后台失败 \(step.title): \(error.localizedDescription)")
                DispatchQueue.main.async { [weak self] in
                    self?.isRunning = false
                    self?.status = "单步失败：\(step.title)"
                    self?.appendLog("单步排查 FAIL \(step.title): \(error.localizedDescription)")
                }
            }
        }
    }

    /// 取消正在运行的实验。
    func cancel() {
        appendLog("当前轻量实验不做中途取消；等待本轮 8 秒录制结束")
    }

    /// 播放最近一次实验生成的 WAV 文件。
    ///
    /// 主要逻辑：直接播放 `wavPath` 指向的手机沙盒文件，用于主观检查 AEC 后录音内容。
    /// 参数：无。
    /// 返回值：无。
    /// 异常情况：文件不存在或播放失败时写入日志。
    func playLastWAV() {
        guard wavPath != "-" else {
            appendLog("没有可播放的 WAV")
            return
        }
        let url = URL(fileURLWithPath: wavPath)
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

    /// 清空页面日志。
    func clearLogs() {
        logs.removeAll()
        logStore.clear()
        appendLog("日志已清空")
    }

    /// 复制全部实验日志到剪贴板。
    func copyLogs() {
        UIPasteboard.general.string = logs.joined(separator: "\n")
        appendLog("日志已复制")
    }

    private func appendLog(_ message: String) {
        let line = "\(Self.timestamp()) \(message)"
        logs.append(line)
        if logs.count > 300 {
            logs.removeFirst(logs.count - 300)
        }
        writeLogLine(line)
    }

    private func writeLogLine(_ line: String) {
        logStore.append(line)
    }

    private func loadLogsFromDisk() {
        logs = logStore.loadTail(limit: 300)
    }

    nonisolated private static func timestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss.SSS"
        return formatter.string(from: Date())
    }
}
