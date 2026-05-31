import AVFoundation
import Foundation

/// 播放链路实验场景。
///
/// 主要功能：把 Voice Processing、水位线、cancel 和真人插话提示组合成可重复运行的实验项。
enum PlaybackExperimentScenario: String, CaseIterable, Identifiable, Sendable {
    case tinyBufferVoiceProcessingOn
    case tinyBufferVoiceProcessingOff
    case voiceChatVoiceProcessingOff
    case defaultBufferVoiceProcessingOn
    case largeBufferVoiceProcessingOn
    case defaultBufferCancelNoSpeech
    case largeBufferCancelNoSpeech
    case userSpeechNoCancel
    case userSpeechWithCancel
    case userSpeechCancelThenTailCapture

    var id: String { rawValue }

    var title: String {
        switch self {
        case .tinyBufferVoiceProcessingOn:
            return "A1 VP开 极小buffer"
        case .tinyBufferVoiceProcessingOff:
            return "A2 Raw 极小buffer"
        case .voiceChatVoiceProcessingOff:
            return "A3 voiceChat VP关"
        case .defaultBufferVoiceProcessingOn:
            return "B1 VP开 默认buffer"
        case .largeBufferVoiceProcessingOn:
            return "B2 VP开 大buffer"
        case .defaultBufferCancelNoSpeech:
            return "C1 默认buffer cancel"
        case .largeBufferCancelNoSpeech:
            return "C2 大buffer cancel"
        case .userSpeechNoCancel:
            return "D1 真人插话不cancel"
        case .userSpeechWithCancel:
            return "D2 真人插话后cancel"
        case .userSpeechCancelThenTailCapture:
            return "D3 cancel后继续录音"
        }
    }

    var description: String {
        switch self {
        case .tinyBufferVoiceProcessingOn:
            return "Voice Processing 开，极小水位线，正常播完；用于确认 server 拉取链路不破坏 AEC。"
        case .tinyBufferVoiceProcessingOff:
            return "Raw playAndRecord，极小水位线，正常播完；用于证明设备和摆位能录到喇叭回声。"
        case .voiceChatVoiceProcessingOff:
            return "voiceChat 模式下关闭 input voice processing；用于观察仅关闭开关时是否仍有系统语音处理。"
        case .defaultBufferVoiceProcessingOn:
            return "Voice Processing 开，默认水位线，正常播完；用于验证正式默认 buffer。"
        case .largeBufferVoiceProcessingOn:
            return "Voice Processing 开，压力水位线，正常播完；用于观察大 buffer 延迟和 drain。"
        case .defaultBufferCancelNoSpeech:
            return "默认水位线，播放约 1 秒后自动 cancel；测试无真人插话时的资源清理。"
        case .largeBufferCancelNoSpeech:
            return "压力水位线，播放约 1 秒后自动 cancel；测试最坏 buffer 清理。"
        case .userSpeechNoCancel:
            return "播放开始后请真人说“打断一下”，不自动 cancel；验证真实语音可被 VAD 捕获。"
        case .userSpeechWithCancel:
            return "播放开始后请真人说“打断一下”，实时 VAD 触发后 cancel；验证真实 VAD 打断链路。"
        case .userSpeechCancelThenTailCapture:
            return "播放开始后请持续说一句话，自动 cancel 后继续录音；验证停播后的用户语音是否恢复清晰。"
        }
    }

    var voiceProcessingEnabled: Bool {
        switch self {
        case .tinyBufferVoiceProcessingOff, .voiceChatVoiceProcessingOff:
            return false
        default:
            return true
        }
    }

    var audioSessionProfile: AudioSessionProfile {
        switch self {
        case .tinyBufferVoiceProcessingOff:
            return .rawPlaybackAndRecord
        default:
            return .voiceProcessing
        }
    }

    var recorderVoiceProcessingSetting: Bool? {
        switch audioSessionProfile {
        case .voiceProcessing:
            return voiceProcessingEnabled
        case .rawPlaybackAndRecord:
            return nil
        }
    }

    var bufferConfiguration: PlaybackBufferConfiguration {
        switch self {
        case .tinyBufferVoiceProcessingOn, .tinyBufferVoiceProcessingOff, .voiceChatVoiceProcessingOff:
            return PlaybackBufferConfiguration(startWatermarkMS: 40, lowWatermarkMS: 80, highWatermarkMS: 160, maxBufferMS: 320)
        case .largeBufferVoiceProcessingOn, .largeBufferCancelNoSpeech:
            return PlaybackBufferConfiguration(startWatermarkMS: 600, lowWatermarkMS: 3000, highWatermarkMS: 12000, maxBufferMS: 20000)
        default:
            return .default
        }
    }

    var cancelAfterPlaybackStartedMS: Int? {
        switch self {
        case .defaultBufferCancelNoSpeech, .largeBufferCancelNoSpeech:
            return 1000
        default:
            return nil
        }
    }

    var waitsForRealtimeSpeechStopAfterCancel: Bool {
        switch self {
        case .userSpeechCancelThenTailCapture:
            return true
        default:
            return false
        }
    }

    var speechStopWaitTimeoutMS: Int {
        switch self {
        case .userSpeechCancelThenTailCapture:
            return 8_000
        default:
            return 0
        }
    }

    var userSpeechPrompt: String? {
        switch self {
        case .userSpeechNoCancel, .userSpeechWithCancel:
            return "请在听到播放开始后约 1 秒说：打断一下"
        case .userSpeechCancelThenTailCapture:
            return "请在听到播放开始后持续说一句话，cancel 后继续说完整句子"
        default:
            return nil
        }
    }

    var usesRealtimeVADInterrupt: Bool {
        switch self {
        case .userSpeechWithCancel, .userSpeechCancelThenTailCapture:
            return true
        default:
            return false
        }
    }
}

/// iOS 音频会话模式。
///
/// 主要功能：把 AEC 正向实验和无处理负对照拆成两个明确的系统音频 profile。
enum AudioSessionProfile: String, Sendable {
    case voiceProcessing
    case rawPlaybackAndRecord

    var mode: AVAudioSession.Mode {
        switch self {
        case .voiceProcessing:
            return .voiceChat
        case .rawPlaybackAndRecord:
            return .default
        }
    }
}

/// 播放 buffer 水位线配置。
struct PlaybackBufferConfiguration: Sendable, Codable {
    var startWatermarkMS: Int
    var lowWatermarkMS: Int
    var highWatermarkMS: Int
    var maxBufferMS: Int

    static let `default` = PlaybackBufferConfiguration(
        startWatermarkMS: 120,
        lowWatermarkMS: 300,
        highWatermarkMS: 800,
        maxBufferMS: 1200
    )
}

/// server 返回的音频格式。
struct ExperimentAudioFormat: Codable, Sendable {
    let codec: String
    let sampleRate: Int
    let channels: Int
    let chunkMS: Int

    enum CodingKeys: String, CodingKey {
        case codec
        case sampleRate = "sample_rate"
        case channels
        case chunkMS = "chunk_ms"
    }
}

/// server 音频 session 信息。
struct ExperimentAudioSessionInfo: Codable, Sendable {
    let ok: Bool
    let sessionID: String
    let format: ExperimentAudioFormat
    let totalChunks: Int
    let totalDurationMS: Int

    enum CodingKeys: String, CodingKey {
        case ok
        case sessionID = "session_id"
        case format
        case totalChunks = "total_chunks"
        case totalDurationMS = "total_duration_ms"
    }
}

/// 可播放的音频 chunk。
struct ExperimentAudioChunk: Codable, Sendable, Equatable {
    let seq: Int
    let durationMS: Int
    let payloadBase64: String
    let final: Bool

    enum CodingKeys: String, CodingKey {
        case seq
        case durationMS = "duration_ms"
        case payloadBase64 = "payload_base64"
        case final
    }

    var payload: Data {
        Data(base64Encoded: payloadBase64) ?? Data()
    }
}

/// chunk 拉取响应。
struct ExperimentChunkResponse: Codable, Sendable {
    let ok: Bool
    let sessionID: String
    let chunks: [ExperimentAudioChunk]
    let nextSeq: Int
    let serverFinished: Bool

    enum CodingKeys: String, CodingKey {
        case ok
        case sessionID = "session_id"
        case chunks
        case nextSeq = "next_seq"
        case serverFinished = "server_finished"
    }
}

/// VAD 服务响应。
struct ExperimentVADResponse: Codable, Sendable {
    let ok: Bool
    let triggered: Bool
    let speechFrames: Int
    let totalFrames: Int
    let firstSpeechMS: Int?
    let speechRatio: Double
    let backend: String
    let speechStartedCount: Int
    let speechStoppedCount: Int
    let asrTexts: [String]

    enum CodingKeys: String, CodingKey {
        case ok
        case triggered
        case speechFrames = "speech_frames"
        case totalFrames = "total_frames"
        case firstSpeechMS = "first_speech_ms"
        case speechRatio = "speech_ratio"
        case backend
        case speechStartedCount = "speech_started_count"
        case speechStoppedCount = "speech_stopped_count"
        case asrTexts = "asr_texts"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        ok = try container.decode(Bool.self, forKey: .ok)
        triggered = try container.decode(Bool.self, forKey: .triggered)
        speechFrames = try container.decode(Int.self, forKey: .speechFrames)
        totalFrames = try container.decode(Int.self, forKey: .totalFrames)
        firstSpeechMS = try container.decodeIfPresent(Int.self, forKey: .firstSpeechMS)
        speechRatio = try container.decode(Double.self, forKey: .speechRatio)
        backend = try container.decode(String.self, forKey: .backend)
        speechStartedCount = try container.decodeIfPresent(Int.self, forKey: .speechStartedCount) ?? 0
        speechStoppedCount = try container.decodeIfPresent(Int.self, forKey: .speechStoppedCount) ?? 0
        asrTexts = try container.decodeIfPresent([String].self, forKey: .asrTexts) ?? []
    }

    var summary: String {
        var parts = [
            "triggered=\(triggered)",
            "speech_frames=\(speechFrames)/\(totalFrames)",
            "ratio=\(String(format: "%.3f", speechRatio))",
            "first_speech_ms=\(firstSpeechMS.map(String.init) ?? "-")",
            "backend=\(backend)",
        ]
        if speechStartedCount > 0 || speechStoppedCount > 0 {
            parts.append("speech_started=\(speechStartedCount)")
            parts.append("speech_stopped=\(speechStoppedCount)")
        }
        if !asrTexts.isEmpty {
            parts.append("asr_text=\(asrTexts.joined(separator: " | "))")
        }
        return parts.joined(separator: " ")
    }
}

struct RealtimeVADSessionResponse: Codable, Sendable {
    let ok: Bool
    let sessionID: String
    let backend: String
    let vadThreshold: Double?
    let silenceDurationMS: Int?

    enum CodingKeys: String, CodingKey {
        case ok
        case sessionID = "session_id"
        case backend
        case vadThreshold = "vad_threshold"
        case silenceDurationMS = "silence_duration_ms"
    }
}

struct RealtimeVADEvent: Codable, Sendable {
    let seq: Int
    let type: String
    let text: String?
    let message: String?
    let audioMS: Int?
    let recordedAtMS: Int?

    enum CodingKeys: String, CodingKey {
        case seq
        case type
        case text
        case message
        case audioMS = "audio_ms"
        case recordedAtMS = "recorded_at_ms"
    }
}

struct RealtimeVADEventResponse: Codable, Sendable {
    let ok: Bool
    let sessionID: String
    let events: [RealtimeVADEvent]

    enum CodingKeys: String, CodingKey {
        case ok
        case sessionID = "session_id"
        case events
    }
}

/// 单次实验结果。
struct PlaybackExperimentResult: Sendable {
    let runID: String
    let runDirectoryURL: URL
    let wavURL: URL
    let timelineURL: URL
    let routeSummary: String
    let vadSummary: String
    let vadTriggered: Bool
}

/// 实验时间线记录器。
final class ExperimentTimeline: @unchecked Sendable {
    private let lock = NSLock()
    private let startedAt = DispatchTime.now().uptimeNanoseconds
    private var events: [[String: Any]] = []
    private var values: [String: Any] = [:]

    func mark(_ event: String, fields: [String: Any] = [:]) {
        lock.lock()
        var record = fields
        record["event"] = event
        record["elapsed_ms"] = elapsedMS()
        events.append(record)
        lock.unlock()
    }

    func set(_ key: String, _ value: Any) {
        lock.lock()
        values[key] = value
        lock.unlock()
    }

    func write(to url: URL) throws {
        lock.lock()
        let payload: [String: Any] = ["events": events, "values": values]
        lock.unlock()
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url, options: [.atomic])
    }

    private func elapsedMS() -> Int {
        Int((DispatchTime.now().uptimeNanoseconds - startedAt) / 1_000_000)
    }
}

/// 播放链路实验运行器。
///
/// 主要功能：编排音频 session、server chunk 拉取、水位线 buffer、renderer 播放和麦克风录制。
final class PlaybackExperimentRunner: @unchecked Sendable {
    private let cancelLock = NSLock()
    private var cancelRequested = false
    private var cancelReason = "none"

    /// 请求当前实验进入 cancel 路径。
    func requestCancel(reason: String) {
        cancelLock.lock()
        cancelRequested = true
        cancelReason = reason
        cancelLock.unlock()
    }

    /// 执行一次播放链路实验。
    ///
    /// 参数：`scenario` 为场景；`audioServerURL` 为 chunk server 根地址；`vadURL` 为 VAD 上传地址；
    /// `progress` 为轻量日志回调，不能阻塞音频线程。
    /// 返回值：实验结果和产物路径。
    /// 异常情况：权限、音频会话、网络或文件写入失败时抛出。
    func run(
        scenario: PlaybackExperimentScenario,
        audioServerURL: URL,
        vadURL: URL,
        onWAVReady: @escaping @Sendable (URL) -> Void = { _ in },
        progress: @escaping @Sendable (String) -> Void
    ) async throws -> PlaybackExperimentResult {
        resetCancel()
        let runID = "run_\(Self.timestampForFile())_\(scenario.rawValue)"
        let runDirectory = try makeRunDirectory(runID: runID)
        let wavURL = runDirectory.appendingPathComponent("mic.wav")
        let timelineURL = runDirectory.appendingPathComponent("timeline.json")
        let timeline = ExperimentTimeline()
        timeline.set("run_id", runID)
        timeline.set("scenario", scenario.rawValue)
        timeline.set("audio_server_url", audioServerURL.absoluteString)
        timeline.set("vad_url", vadURL.absoluteString)
        timeline.mark("run_started")

        progress("请求麦克风权限")
        try await AudioSessionController.requestMicrophonePermission()
        let audioController = AudioSessionController()
        let audioProfile = scenario.audioSessionProfile
        progress("配置音频会话 profile=\(audioProfile.rawValue) mode=\(audioProfile.mode.rawValue)")
        let route = try audioController.configure(profile: audioProfile)
        timeline.set("route", route)
        timeline.mark("audio_session_configured", fields: [
            "profile": audioProfile.rawValue,
            "mode": audioProfile.mode.rawValue,
            "voice_processing": scenario.voiceProcessingEnabled,
        ])
        if let prompt = scenario.userSpeechPrompt {
            progress(prompt)
            timeline.mark("user_speech_prompt", fields: ["message": prompt])
        }

        let chunkSource = PullingAudioChunkSource(baseURL: audioServerURL, progress: progress)
        progress("创建音频 session")
        let session = try await chunkSource.startSession(scenario: scenario.rawValue)
        progress("音频 session 已创建 id=\(session.sessionID) total_chunks=\(session.totalChunks)")
        timeline.set("audio_format", [
            "codec": session.format.codec,
            "sample_rate": session.format.sampleRate,
            "channels": session.format.channels,
            "chunk_ms": session.format.chunkMS,
        ])
        timeline.set("total_chunks", session.totalChunks)
        timeline.mark("audio_session_created", fields: ["session_id": session.sessionID, "total_chunks": session.totalChunks])

        let realtimeVAD = scenario.usesRealtimeVADInterrupt
            ? RealtimeVADInterruptMonitor(
                vadURL: vadURL,
                continueAfterInterrupt: scenario.waitsForRealtimeSpeechStopAfterCancel,
                progress: progress,
                onInterrupt: { [weak self] reason in
                    self?.requestCancel(reason: reason)
                }
            )
            : nil
        let recorder = MicrophoneCaptureRecorder { samples, sampleRate in
            realtimeVAD?.append(samples: samples, sampleRate: sampleRate)
        }
        if realtimeVAD != nil {
            progress("实时 VAD 监控已准备")
            timeline.mark("realtime_vad_prepared")
        }
        progress("启动麦克风录制")
        try recorder.start(voiceProcessingEnabled: scenario.recorderVoiceProcessingSetting, progress: progress)
        timeline.mark("mic_capture_started")
        progress("麦克风录制已启动")

        let renderer = RingBufferPlaybackRenderer()
        progress("启动播放 renderer sample_rate=\(session.format.sampleRate) chunk_ms=\(session.format.chunkMS)")
        try renderer.prepare(format: session.format)
        timeline.mark("renderer_prepared")
        progress("播放 renderer 已启动")

        let buffer = WatermarkPlaybackBuffer(configuration: scenario.bufferConfiguration)
        let chunkMS = max(1, session.format.chunkMS)
        var lastReceivedSeq = -1
        var serverFinished = false
        var playbackStartedAt: UInt64?
        var chunksReceived = 0
        var highWatermarkActive = false
        var autoCancelFired = false
        var renderedChunks = 0

        let drainTask = Task {
            while !Task.isCancelled {
                if self.isCancelRequested() {
                    return
                }
                if let chunk = await buffer.takeNextDrainableChunk() {
                    try await renderer.write(chunk)
                    renderedChunks += 1
                    if renderedChunks <= 5 || renderedChunks % 50 == 0 {
                        progress("renderer write seq=\(chunk.seq) rendered=\(renderedChunks)")
                    }
                    continue
                }
                if await buffer.isFinishedAndEmpty {
                    return
                }
                try await Task.sleep(nanoseconds: 5_000_000)
            }
        }

        func performCancelCleanup(phase: String) async {
            let reason = currentCancelReason()
            progress("进入 cancel 清理 reason=\(reason) phase=\(phase)")
            timeline.mark("cancel_requested", fields: ["reason": reason, "phase": phase, "last_received_seq": lastReceivedSeq])
            let clearStarted = DispatchTime.now().uptimeNanoseconds
            let discarded = await buffer.cancel()
            timeline.mark("buffer_cleared", fields: [
                "discarded_chunks": discarded.count,
                "elapsed_from_cancel_ms": elapsedMS(since: clearStarted),
            ])
            let rendererCleared = await renderer.cancel()
            timeline.mark("renderer_cleared", fields: rendererCleared)
            progress("停止播放 renderer")
            timeline.mark("renderer_stopped", fields: await renderer.stop())
            try? await chunkSource.cancel(sessionID: session.sessionID, lastReceivedSeq: lastReceivedSeq, reason: reason)
            drainTask.cancel()
            if scenario.waitsForRealtimeSpeechStopAfterCancel, let realtimeVAD {
                let timeoutMS = scenario.speechStopWaitTimeoutMS
                progress("cancel 后等待实时 ASR speech_stopped timeout_ms=\(timeoutMS)")
                timeline.mark("post_cancel_recording_started", fields: ["mode": "wait_speech_stopped", "timeout_ms": timeoutMS])
                let stopped = await realtimeVAD.waitForSpeechStopped(timeoutMS: timeoutMS)
                if stopped {
                    progress("实时 ASR speech_stopped 已收到，停止录音")
                    timeline.mark("post_cancel_recording_completed", fields: ["reason": "speech_stopped"])
                } else {
                    progress("等待 speech_stopped 超时，停止录音")
                    timeline.mark("post_cancel_recording_completed", fields: ["reason": "timeout"])
                }
            }
        }

        do {
            while !serverFinished && !isCancelRequested() {
                try Task.checkCancellation()
                if let cancelAfter = scenario.cancelAfterPlaybackStartedMS,
                   let started = playbackStartedAt,
                   !autoCancelFired,
                   elapsedMS(since: started) >= cancelAfter {
                    autoCancelFired = true
                    requestCancel(reason: "scenario_auto_cancel_after_\(cancelAfter)ms")
                    break
                }

                let snapshot = await buffer.snapshot()
                if snapshot.bufferedMS >= scenario.bufferConfiguration.highWatermarkMS {
                    if !highWatermarkActive {
                        highWatermarkActive = true
                        progress("达到高水位，暂停拉取 buffered_ms=\(snapshot.bufferedMS)")
                        timeline.mark("pull_paused_high_watermark", fields: ["buffered_ms": snapshot.bufferedMS])
                    }
                    try await Task.sleep(nanoseconds: 20_000_000)
                    continue
                }
                if highWatermarkActive && snapshot.bufferedMS <= scenario.bufferConfiguration.lowWatermarkMS {
                    highWatermarkActive = false
                    progress("低于低水位，恢复拉取 buffered_ms=\(snapshot.bufferedMS)")
                    timeline.mark("pull_resumed_low_watermark", fields: ["buffered_ms": snapshot.bufferedMS])
                }

                let availableMS = max(chunkMS, scenario.bufferConfiguration.maxBufferMS - snapshot.bufferedMS)
                let limit = max(1, min(32, availableMS / chunkMS))
                let response = try await chunkSource.pull(sessionID: session.sessionID, afterSeq: lastReceivedSeq, limit: limit)
                if response.chunks.isEmpty {
                    serverFinished = response.serverFinished
                    try await Task.sleep(nanoseconds: 20_000_000)
                    continue
                }
                chunksReceived += response.chunks.count
                for chunk in response.chunks {
                    let actions = await buffer.append(chunk)
                    lastReceivedSeq = max(lastReceivedSeq, chunk.seq)
                    if actions.contains(.started), playbackStartedAt == nil {
                        playbackStartedAt = DispatchTime.now().uptimeNanoseconds
                        progress("播放开始 seq=\(chunk.seq)")
                        timeline.mark("playback_started", fields: ["seq": chunk.seq])
                        realtimeVAD?.activate()
                    }
                    if chunk.final {
                        serverFinished = true
                    }
                }
                if chunksReceived <= 5 || chunksReceived % 50 == 0 {
                    progress("拉取 chunk count=\(chunksReceived) last_seq=\(lastReceivedSeq)")
                }
            }

            if isCancelRequested() {
                await performCancelCleanup(phase: "pull_loop")
            } else {
                progress("server 音频结束，等待 drain")
                await buffer.markFinished(expectedLastSeq: lastReceivedSeq)
                timeline.mark("finish_marked", fields: ["expected_last_seq": lastReceivedSeq])
                try await drainTask.value
                if isCancelRequested() {
                    await performCancelCleanup(phase: "buffer_drain")
                } else {
                    let drainCompleted = try await renderer.drain(shouldCancel: { self.isCancelRequested() })
                    if drainCompleted {
                        timeline.mark("drain_completed")
                        progress("停止播放 renderer")
                        timeline.mark("renderer_stopped", fields: await renderer.stop())
                    } else {
                        await performCancelCleanup(phase: "renderer_drain")
                    }
                }
            }
        } catch {
            drainTask.cancel()
            _ = await buffer.cancel()
            _ = await renderer.cancel()
            _ = await renderer.stop()
            realtimeVAD?.stop()
            throw error
        }

        realtimeVAD?.stop()
        progress("停止麦克风录制")
        let capture = recorder.stop()
        timeline.mark("mic_capture_stopped", fields: ["buffers": capture.buffers.count])
        progress("写入 WAV")
        try WAVWriter.write(capture: capture, to: wavURL)
        timeline.mark("wav_written", fields: ["path": wavURL.path])
        onWAVReady(wavURL)
        let vadSummary = scenario.usesRealtimeVADInterrupt
            ? "实时 VAD 已用于打断；未执行离线 VAD"
            : "未执行离线 VAD"
        let vadTriggered = isCancelRequested() && currentCancelReason() == "vad_interrupt"
        progress("跳过离线 VAD，实验完成条件为 WAV 写入完成")
        timeline.mark("offline_vad_skipped", fields: ["reason": "realtime_or_manual_review_only"])
        timeline.set("playback", [
            "chunks_received": chunksReceived,
            "chunks_rendered": renderedChunks,
            "last_received_seq": lastReceivedSeq,
            "buffer": [
                "start_watermark_ms": scenario.bufferConfiguration.startWatermarkMS,
                "low_watermark_ms": scenario.bufferConfiguration.lowWatermarkMS,
                "high_watermark_ms": scenario.bufferConfiguration.highWatermarkMS,
                "max_buffer_ms": scenario.bufferConfiguration.maxBufferMS,
            ],
            "renderer": await renderer.diagnostics(),
        ])
        do {
            try audioController.deactivate()
            timeline.mark("audio_session_deactivated")
        } catch {
            progress("音频会话释放失败，已忽略：\(error.localizedDescription)")
            timeline.mark("audio_session_deactivate_failed", fields: ["error": error.localizedDescription])
        }
        try timeline.write(to: timelineURL)

        return PlaybackExperimentResult(
            runID: runID,
            runDirectoryURL: runDirectory,
            wavURL: wavURL,
            timelineURL: timelineURL,
            routeSummary: route,
            vadSummary: vadSummary,
            vadTriggered: vadTriggered
        )
    }

    private func resetCancel() {
        cancelLock.lock()
        cancelRequested = false
        cancelReason = "none"
        cancelLock.unlock()
    }

    private func isCancelRequested() -> Bool {
        cancelLock.lock()
        let value = cancelRequested
        cancelLock.unlock()
        return value
    }

    private func currentCancelReason() -> String {
        cancelLock.lock()
        let value = cancelReason
        cancelLock.unlock()
        return value
    }

    private func makeRunDirectory(runID: String) throws -> URL {
        let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        let root = documentsURL.appendingPathComponent("playback-chain", isDirectory: true)
        let runDirectory = root.appendingPathComponent(runID, isDirectory: true)
        try FileManager.default.createDirectory(at: runDirectory, withIntermediateDirectories: true)
        return runDirectory
    }

    private func elapsedMS(since start: UInt64) -> Int {
        Int((DispatchTime.now().uptimeNanoseconds - start) / 1_000_000)
    }

    private static func timestampForFile() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter.string(from: Date())
    }
}

/// 播放 buffer 触发的动作。
enum PlaybackBufferAction: Equatable, Sendable {
    case started
}

/// 播放 buffer 诊断快照。
struct PlaybackBufferSnapshot: Sendable {
    let bufferedMS: Int
    let queuedChunks: Int
}

/// 水位线播放 buffer。
///
/// 主要功能：按 seq 暂存 server 拉取的音频 chunk，并在达到起播水位后供 renderer 连续取出。
actor WatermarkPlaybackBuffer {
    private let configuration: PlaybackBufferConfiguration
    private var pending: [Int: ExperimentAudioChunk] = [:]
    private var nextDrainSeq: Int?
    private var bufferedMS = 0
    private var started = false
    private var finished = false
    private var expectedLastSeq: Int?

    init(configuration: PlaybackBufferConfiguration) {
        self.configuration = configuration
    }

    func append(_ chunk: ExperimentAudioChunk) -> [PlaybackBufferAction] {
        guard pending[chunk.seq] == nil else { return [] }
        pending[chunk.seq] = chunk
        if nextDrainSeq == nil {
            nextDrainSeq = chunk.seq
        }
        bufferedMS += max(0, chunk.durationMS)
        var actions: [PlaybackBufferAction] = []
        if !started && bufferedMS >= configuration.startWatermarkMS {
            started = true
            actions.append(.started)
        }
        return actions
    }

    func takeNextDrainableChunk() -> ExperimentAudioChunk? {
        guard started else { return nil }
        guard let seq = nextDrainSeq ?? pending.keys.min(), let chunk = pending.removeValue(forKey: seq) else {
            return nil
        }
        nextDrainSeq = seq + 1
        bufferedMS = max(0, bufferedMS - max(0, chunk.durationMS))
        return chunk
    }

    func markFinished(expectedLastSeq: Int?) {
        finished = true
        self.expectedLastSeq = expectedLastSeq
    }

    var isFinishedAndEmpty: Bool {
        if let expectedLastSeq, (nextDrainSeq ?? 0) <= expectedLastSeq {
            return false
        }
        return finished && pending.isEmpty
    }

    func cancel() -> [ExperimentAudioChunk] {
        let chunks = Array(pending.values)
        pending.removeAll()
        nextDrainSeq = nil
        bufferedMS = 0
        started = false
        finished = true
        return chunks
    }

    func snapshot() -> PlaybackBufferSnapshot {
        PlaybackBufferSnapshot(bufferedMS: bufferedMS, queuedChunks: pending.count)
    }
}

/// HTTP 音频 chunk 拉取客户端。
struct PullingAudioChunkSource: Sendable {
    let baseURL: URL
    let progress: @Sendable (String) -> Void

    func startSession(scenario: String) async throws -> ExperimentAudioSessionInfo {
        var request = URLRequest(url: baseURL.appendingPathComponent("audio/sessions"))
        request.httpMethod = "POST"
        request.timeoutInterval = 5
        request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["scenario": scenario, "repeat": 1])
        progress("HTTP POST \(request.url?.absoluteString ?? "-")")
        let data = try await send(request)
        let session = try JSONDecoder().decode(ExperimentAudioSessionInfo.self, from: data)
        progress("HTTP session response chunks=\(session.totalChunks) format=\(session.format.codec)/\(session.format.sampleRate)")
        return session
    }

    func pull(sessionID: String, afterSeq: Int, limit: Int) async throws -> ExperimentChunkResponse {
        var components = URLComponents(url: baseURL.appendingPathComponent("audio/sessions/\(sessionID)/chunks"), resolvingAgainstBaseURL: false)!
        components.queryItems = [
            URLQueryItem(name: "after_seq", value: String(afterSeq)),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = 5
        let data = try await send(request)
        let response = try JSONDecoder().decode(ExperimentChunkResponse.self, from: data)
        if afterSeq < 0 || response.chunks.contains(where: { $0.final }) {
            progress("HTTP chunk response after_seq=\(afterSeq) count=\(response.chunks.count) finished=\(response.serverFinished)")
        }
        return response
    }

    func cancel(sessionID: String, lastReceivedSeq: Int, reason: String) async throws {
        var request = URLRequest(url: baseURL.appendingPathComponent("audio/sessions/\(sessionID)/cancel"))
        request.httpMethod = "POST"
        request.timeoutInterval = 5
        request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "reason": reason,
            "client_last_received_seq": lastReceivedSeq,
        ])
        _ = try await send(request)
    }

    private func send(_ request: URLRequest) async throws -> Data {
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
                let text = String(data: data, encoding: .utf8) ?? ""
                throw PlaybackExperimentError.network("HTTP 请求失败：\(text)")
            }
            return data
        } catch {
            progress("HTTP 失败 \(request.url?.absoluteString ?? "-") error=\(error.localizedDescription)")
            throw error
        }
    }
}

/// VAD HTTP 客户端。
struct VADClient: Sendable {
    let vadURL: URL
    let timeoutInterval: TimeInterval

    init(vadURL: URL, timeoutInterval: TimeInterval = 90) {
        self.vadURL = vadURL
        self.timeoutInterval = timeoutInterval
    }

    func analyze(wavURL: URL) async throws -> ExperimentVADResponse {
        try await analyze(wavData: Data(contentsOf: wavURL))
    }

    func analyze(wavData: Data) async throws -> ExperimentVADResponse {
        var request = URLRequest(url: vadURL)
        request.httpMethod = "POST"
        request.timeoutInterval = timeoutInterval
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.httpBody = wavData
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            let text = String(data: data, encoding: .utf8) ?? ""
            throw PlaybackExperimentError.network("VAD 请求失败：\(text)")
        }
        return try JSONDecoder().decode(ExperimentVADResponse.self, from: data)
    }
}

struct RealtimeVADClient: Sendable {
    let sessionURL: URL

    init(vadAnalyzeURL: URL) {
        var components = URLComponents(url: vadAnalyzeURL, resolvingAgainstBaseURL: false)
        components?.path = "/vad/realtime/sessions"
        components?.query = nil
        sessionURL = components?.url ?? vadAnalyzeURL
    }

    func start(sampleRate: Int) async throws -> RealtimeVADSessionResponse {
        var request = URLRequest(url: sessionURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 12
        request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "sample_rate": sampleRate,
            "format": "pcm16le",
            "channels": 1,
        ])
        return try await send(request, as: RealtimeVADSessionResponse.self)
    }

    func append(sessionID: String, pcm: Data, afterSeq: Int) async throws -> RealtimeVADEventResponse {
        let url = sessionURL.appendingPathComponent(sessionID).appendingPathComponent("chunks")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 3
        request.setValue("audio/pcm; codec=pcm16le; rate=16000", forHTTPHeaderField: "Content-Type")
        request.setValue(String(afterSeq), forHTTPHeaderField: "X-After-Event-Seq")
        request.httpBody = pcm
        return try await send(request, as: RealtimeVADEventResponse.self)
    }

    func finish(sessionID: String) async {
        let url = sessionURL.appendingPathComponent(sessionID).appendingPathComponent("finish")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 3
        _ = try? await URLSession.shared.data(for: request)
    }

    private func send<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            let text = String(data: data, encoding: .utf8) ?? ""
            throw PlaybackExperimentError.network("实时 VAD 请求失败：\(text)")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

/// 实时 VAD 打断监控器。
///
/// 主要功能：从麦克风 tap 接收已被 iOS Voice Processing/AEC 处理过的短音频 chunk，
/// 立即发送给服务端实时 VAD；一旦服务端返回 `speech_started` 就请求播放 cancel。
final class RealtimeVADInterruptMonitor: @unchecked Sendable {
    private let client: RealtimeVADClient
    private let continueAfterInterrupt: Bool
    private let progress: @Sendable (String) -> Void
    private let onInterrupt: @Sendable (String) -> Void
    private let lock = NSLock()
    private var active = false
    private var stopped = false
    private var inFlight = false
    private var triggered = false
    private var speechStopped = false
    private var sessionID: String?
    private var samples: [Float] = []
    private var inputSampleRate: Double = 48_000
    private var lastEventSeq = -1
    private var sentChunkCount = 0
    private let chunkMS = 100

    init(
        vadURL: URL,
        continueAfterInterrupt: Bool,
        progress: @escaping @Sendable (String) -> Void,
        onInterrupt: @escaping @Sendable (String) -> Void
    ) {
        self.client = RealtimeVADClient(vadAnalyzeURL: vadURL)
        self.continueAfterInterrupt = continueAfterInterrupt
        self.progress = progress
        self.onInterrupt = onInterrupt
    }

    func activate() {
        lock.lock()
        active = true
        stopped = false
        lock.unlock()
        progress("实时 VAD 启动中")
        Task.detached(priority: .utility) { [weak self] in
            await self?.startSessionIfNeeded()
        }
    }

    func stop() {
        let currentSession: String?
        lock.lock()
        stopped = true
        active = false
        samples.removeAll()
        currentSession = sessionID
        sessionID = nil
        lock.unlock()
        if let currentSession {
            Task.detached(priority: .utility) { [client] in
                await client.finish(sessionID: currentSession)
            }
        }
    }

    func waitForSpeechStopped(timeoutMS: Int) async -> Bool {
        let startedAt = DispatchTime.now().uptimeNanoseconds
        while !Task.isCancelled {
            lock.lock()
            let completed = speechStopped
            lock.unlock()
            if completed {
                return true
            }
            if Int((DispatchTime.now().uptimeNanoseconds - startedAt) / 1_000_000) >= timeoutMS {
                return false
            }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return false
    }

    func append(samples newSamples: [Float], sampleRate: Double) {
        let segment: [Float]?
        lock.lock()
        if !stopped && active {
            inputSampleRate = sampleRate
            samples.append(contentsOf: newSamples)
        }
        let windowCount = max(1, Int(sampleRate * Double(chunkMS) / 1000.0))
        if active && !stopped && sessionID != nil && !inFlight && samples.count >= windowCount {
            inFlight = true
            segment = Array(samples.prefix(windowCount))
            samples.removeFirst(windowCount)
        } else {
            segment = nil
        }
        lock.unlock()

        guard let segment else { return }
        Task.detached(priority: .utility) { [weak self] in
            await self?.send(segment: segment, sampleRate: sampleRate)
        }
    }

    private func startSessionIfNeeded() async {
        lock.lock()
        let shouldStart = active && !stopped && sessionID == nil
        lock.unlock()
        guard shouldStart else { return }
        do {
            let response = try await client.start(sampleRate: 16_000)
            lock.lock()
            if !stopped {
                sessionID = response.sessionID
            }
            lock.unlock()
            let thresholdText = response.vadThreshold.map { String(format: "%.2f", $0) } ?? "-"
            let silenceText = response.silenceDurationMS.map(String.init) ?? "-"
            progress("实时 VAD session 已创建 id=\(response.sessionID) backend=\(response.backend) threshold=\(thresholdText) silence_ms=\(silenceText)")
        } catch {
            progress("实时 VAD session 创建失败：\(error.localizedDescription)")
        }
    }

    private func send(segment: [Float], sampleRate: Double) async {
        let currentSession: String?
        let afterSeq: Int
        lock.lock()
        currentSession = sessionID
        afterSeq = lastEventSeq
        lock.unlock()
        guard let currentSession else {
            markSendCompleted()
            return
        }
        do {
            let pcm = WAVWriter.makePCM(samples: segment, inputSampleRate: sampleRate, outputSampleRate: 16_000)
            let level = Self.audioLevel(samples: segment)
            let sendStarted = DispatchTime.now().uptimeNanoseconds
            let response = try await client.append(sessionID: currentSession, pcm: pcm, afterSeq: afterSeq)
            let elapsedMS = Int((DispatchTime.now().uptimeNanoseconds - sendStarted) / 1_000_000)
            lock.lock()
            sentChunkCount += 1
            let chunkCount = sentChunkCount
            lock.unlock()
            if !response.events.isEmpty || chunkCount <= 3 || chunkCount % 10 == 0 || level.rms >= 0.01 {
                progress(
                    "实时 VAD chunk ack count=\(chunkCount) events=\(response.events.count) http_ms=\(elapsedMS) " +
                    "rms=\(String(format: "%.4f", level.rms)) peak=\(String(format: "%.4f", level.peak))"
                )
            }
            handle(events: response.events)
        } catch {
            progress("实时 VAD chunk 发送失败：\(error.localizedDescription)")
        }
        markSendCompleted()
    }

    private func handle(events: [RealtimeVADEvent]) {
        for event in events {
            lock.lock()
            lastEventSeq = max(lastEventSeq, event.seq)
            lock.unlock()
            switch event.type {
            case "speech_started":
                lock.lock()
                let shouldInterrupt = !triggered && !stopped
                triggered = true
                active = continueAfterInterrupt
                lock.unlock()
                if shouldInterrupt {
                    progress("实时 VAD speech_started，触发打断 event_seq=\(event.seq) audio_ms=\(event.audioMS.map(String.init) ?? "-")")
                    onInterrupt("vad_interrupt")
                }
            case "speech_stopped":
                lock.lock()
                speechStopped = true
                active = false
                lock.unlock()
                progress("实时 VAD speech_stopped")
            case "asr_text":
                if let text = event.text, !text.isEmpty {
                    progress("实时 ASR 文本：\(text)")
                }
            case "error":
                progress("实时 VAD 服务错误：\(event.message ?? event.text ?? "-")")
            default:
                break
            }
        }
    }

    private func markSendCompleted() {
        lock.lock()
        inFlight = false
        lock.unlock()
    }

    private static func audioLevel(samples: [Float]) -> (rms: Double, peak: Double) {
        guard !samples.isEmpty else { return (0, 0) }
        var sumSquares = 0.0
        var peak = 0.0
        for sample in samples {
            let value = Double(sample)
            sumSquares += value * value
            peak = max(peak, abs(value))
        }
        return (sqrt(sumSquares / Double(samples.count)), peak)
    }

}

/// iOS 音频会话控制器。
final class AudioSessionController: @unchecked Sendable {
    static func requestMicrophonePermission() async throws {
        let granted = await withCheckedContinuation { continuation in
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                continuation.resume(returning: granted)
            }
        }
        guard granted else {
            throw PlaybackExperimentError.permissionDenied("麦克风权限被拒绝")
        }
    }

    func configure(profile: AudioSessionProfile) throws -> String {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: profile.mode, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setPreferredSampleRate(16_000)
        try session.setPreferredIOBufferDuration(0.02)
        try session.setActive(true)
        return Self.routeSummary(session.currentRoute)
    }

    func deactivate() throws {
        try AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
    }

    private static func routeSummary(_ route: AVAudioSessionRouteDescription) -> String {
        let inputs = route.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        let outputs = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        return "inputs[\(inputs)] outputs[\(outputs)]"
    }
}

/// 麦克风捕获结果。
struct CapturedAudio: Sendable {
    let sampleRate: Double
    let channels: Int
    let buffers: [[Float]]
}

/// 麦克风录制器。
///
/// 主要功能：录制 Voice Processing 后的 input tap；tap 内只复制 Float 样本，避免影响主流程。
final class MicrophoneCaptureRecorder: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private let lock = NSLock()
    private let onBuffer: @Sendable ([Float], Double) -> Void
    private var buffers: [[Float]] = []
    private var sampleRate: Double = 48_000
    private var channels = 1

    init(onBuffer: @escaping @Sendable ([Float], Double) -> Void = { _, _ in }) {
        self.onBuffer = onBuffer
    }

    func start(voiceProcessingEnabled: Bool?, progress: @Sendable (String) -> Void) throws {
        let input = engine.inputNode
        if let voiceProcessingEnabled {
            progress("麦克风 setVoiceProcessingEnabled begin value=\(voiceProcessingEnabled)")
            try input.setVoiceProcessingEnabled(voiceProcessingEnabled)
            progress("麦克风 setVoiceProcessingEnabled done")
        } else {
            progress("麦克风跳过 setVoiceProcessingEnabled，使用 raw input node")
        }
        progress("麦克风读取 input format")
        let format = input.outputFormat(forBus: 0)
        sampleRate = format.sampleRate
        channels = Int(format.channelCount)
        progress("麦克风 format sample_rate=\(Int(format.sampleRate)) channels=\(format.channelCount)")
        let bufferSize = AVAudioFrameCount(max(1, Int(format.sampleRate * 0.02)))
        progress("麦克风安装 tap buffer_size=\(bufferSize)")
        input.installTap(onBus: 0, bufferSize: bufferSize, format: format) { [weak self] buffer, _ in
            self?.append(buffer)
        }
        progress("麦克风 prepare engine")
        engine.prepare()
        progress("麦克风 start engine begin")
        try engine.start()
        progress("麦克风 start engine done")
    }

    func stop() -> CapturedAudio {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        lock.lock()
        let copy = buffers
        buffers.removeAll()
        let result = CapturedAudio(sampleRate: sampleRate, channels: channels, buffers: copy)
        lock.unlock()
        return result
    }

    private func append(_ buffer: AVAudioPCMBuffer) {
        guard let floatChannels = buffer.floatChannelData else { return }
        let frameLength = Int(buffer.frameLength)
        let channelCount = max(1, Int(buffer.format.channelCount))
        var mono = [Float](repeating: 0, count: frameLength)
        for channel in 0..<channelCount {
            for frame in 0..<frameLength {
                mono[frame] += floatChannels[channel][frame] / Float(channelCount)
            }
        }
        lock.lock()
        buffers.append(mono)
        let currentSampleRate = sampleRate
        lock.unlock()
        onBuffer(mono, currentSampleRate)
    }
}

/// WAV 写入工具。
enum WAVWriter {
    static func write(capture: CapturedAudio, to url: URL) throws {
        let pcm = makeMono16kPCM(capture)
        try writeWAV(pcm: pcm, sampleRate: 16_000, channels: 1, to: url)
    }

    static func makeWAV(samples: [Float], inputSampleRate: Double, outputSampleRate: Int) -> Data {
        let pcm = makePCM(samples: samples, inputSampleRate: inputSampleRate, outputSampleRate: outputSampleRate)
        return makeWAVData(pcm: pcm, sampleRate: outputSampleRate, channels: 1)
    }

    static func makePCM(samples: [Float], inputSampleRate: Double, outputSampleRate: Int) -> Data {
        makePCMData(samples: samples, inputSampleRate: inputSampleRate, outputSampleRate: outputSampleRate)
    }

    private static func makeMono16kPCM(_ capture: CapturedAudio) -> Data {
        makePCMData(samples: capture.buffers.flatMap { $0 }, inputSampleRate: capture.sampleRate, outputSampleRate: 16_000)
    }

    private static func makePCMData(samples: [Float], inputSampleRate: Double, outputSampleRate: Int) -> Data {
        let inputRate = max(inputSampleRate, 1)
        let step = max(1, Int(round(inputRate / Double(outputSampleRate))))
        var pcm = Data()
        for frame in stride(from: 0, to: samples.count, by: step) {
            let clamped = max(-1.0, min(1.0, samples[frame]))
            var value = Int16(clamped * 32767).littleEndian
            withUnsafeBytes(of: &value) { pcm.append(contentsOf: $0) }
        }
        return pcm
    }

    private static func writeWAV(pcm: Data, sampleRate: Int, channels: Int, to url: URL) throws {
        try makeWAVData(pcm: pcm, sampleRate: sampleRate, channels: channels).write(to: url, options: [.atomic])
    }

    private static func makeWAVData(pcm: Data, sampleRate: Int, channels: Int) -> Data {
        var data = Data()
        let byteRate = sampleRate * channels * 2
        let blockAlign = channels * 2
        data.append("RIFF".data(using: .ascii)!)
        data.append(UInt32(36 + pcm.count).littleEndianData)
        data.append("WAVE".data(using: .ascii)!)
        data.append("fmt ".data(using: .ascii)!)
        data.append(UInt32(16).littleEndianData)
        data.append(UInt16(1).littleEndianData)
        data.append(UInt16(channels).littleEndianData)
        data.append(UInt32(sampleRate).littleEndianData)
        data.append(UInt32(byteRate).littleEndianData)
        data.append(UInt16(blockAlign).littleEndianData)
        data.append(UInt16(16).littleEndianData)
        data.append("data".data(using: .ascii)!)
        data.append(UInt32(pcm.count).littleEndianData)
        data.append(pcm)
        return data
    }
}

/// ring buffer 播放 renderer。
final class RingBufferPlaybackRenderer: @unchecked Sendable {
    private let lock = NSLock()
    private let engine = AVAudioEngine()
    private var sourceNode: AVAudioSourceNode?
    private var ring = FloatRingBuffer(capacityFrames: 24_000 * 30)
    private var format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24_000, channels: 1, interleaved: false)!
    private var underrunEvents = 0
    private var droppedFrames = 0

    func prepare(format audioFormat: ExperimentAudioFormat) throws {
        guard audioFormat.codec == "pcm16le" else {
            throw PlaybackExperimentError.audio("不支持的播放 codec：\(audioFormat.codec)")
        }
        guard let playbackFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(audioFormat.sampleRate),
            channels: AVAudioChannelCount(audioFormat.channels),
            interleaved: false
        ) else {
            throw PlaybackExperimentError.audio("无法创建播放格式")
        }
        lock.lock()
        format = playbackFormat
        ring = FloatRingBuffer(capacityFrames: audioFormat.sampleRate * 30)
        lock.unlock()
        engine.stop()
        if let sourceNode {
            engine.detach(sourceNode)
        }
        let node = AVAudioSourceNode { [weak self] _, _, frameCount, audioBufferList -> OSStatus in
            self?.render(frameCount: frameCount, audioBufferList: audioBufferList)
            return noErr
        }
        sourceNode = node
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: playbackFormat)
        engine.prepare()
        try engine.start()
    }

    func write(_ chunk: ExperimentAudioChunk) async throws {
        let samples = Self.floatSamples(fromPCM16LE: chunk.payload)
        lock.lock()
        let dropped = ring.append(samples)
        droppedFrames += dropped
        lock.unlock()
    }

    func drain() async throws {
        _ = try await drain(shouldCancel: { false })
    }

    func drain(shouldCancel: @escaping @Sendable () -> Bool) async throws -> Bool {
        while bufferedFrames > 0 {
            if shouldCancel() {
                return false
            }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        let tailChecks = 6
        for _ in 0..<tailChecks {
            if shouldCancel() {
                return false
            }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        return true
    }

    func cancel() async -> [String: Any] {
        lock.lock()
        let cleared = ring.bufferedFrames
        ring.reset()
        lock.unlock()
        return ["ring_frames_cleared": cleared]
    }

    func stop() async -> [String: Any] {
        lock.lock()
        let cleared = ring.bufferedFrames
        ring.reset()
        lock.unlock()
        engine.stop()
        if let sourceNode {
            engine.detach(sourceNode)
            self.sourceNode = nil
        }
        return ["ring_frames_cleared": cleared, "engine_running": engine.isRunning]
    }

    func diagnostics() async -> [String: Any] {
        lock.lock()
        let data: [String: Any] = [
            "buffered_frames": ring.bufferedFrames,
            "capacity_frames": ring.capacityFrames,
            "underrun_events": underrunEvents,
            "dropped_frames": droppedFrames,
            "engine_running": engine.isRunning,
            "sample_rate": Int(format.sampleRate),
        ]
        lock.unlock()
        return data
    }

    private var bufferedFrames: Int {
        lock.lock()
        let value = ring.bufferedFrames
        lock.unlock()
        return value
    }

    private func render(frameCount: AVAudioFrameCount, audioBufferList: UnsafeMutablePointer<AudioBufferList>) {
        lock.lock()
        let rendered = ring.render(count: Int(frameCount))
        if rendered.underrun {
            underrunEvents += 1
        }
        lock.unlock()
        let buffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
        for bufferIndex in buffers.indices {
            guard let pointer = buffers[bufferIndex].mData?.assumingMemoryBound(to: Float.self) else { continue }
            for frameIndex in 0..<Int(frameCount) {
                pointer[frameIndex] = frameIndex < rendered.samples.count ? rendered.samples[frameIndex] : 0
            }
            buffers[bufferIndex].mDataByteSize = UInt32(Int(frameCount) * MemoryLayout<Float>.size)
        }
    }

    private static func floatSamples(fromPCM16LE data: Data) -> [Float] {
        let sampleCount = data.count / 2
        guard sampleCount > 0 else { return [] }
        return data.withUnsafeBytes { rawBuffer in
            guard let bytes = rawBuffer.bindMemory(to: UInt8.self).baseAddress else { return [] }
            var samples: [Float] = []
            samples.reserveCapacity(sampleCount)
            for index in 0..<sampleCount {
                let low = UInt16(bytes[index * 2])
                let high = UInt16(bytes[index * 2 + 1]) << 8
                let value = Int16(bitPattern: high | low)
                samples.append(Float(value) / 32768.0)
            }
            return samples
        }
    }
}

/// 简单 Float ring buffer。
struct FloatRingBuffer {
    let capacityFrames: Int
    private var storage: [Float]
    private var readIndex = 0
    private var writeIndex = 0
    private(set) var bufferedFrames = 0

    init(capacityFrames: Int) {
        self.capacityFrames = max(1, capacityFrames)
        storage = [Float](repeating: 0, count: max(1, capacityFrames))
    }

    mutating func append(_ samples: [Float]) -> Int {
        var dropped = 0
        for sample in samples {
            if bufferedFrames == capacityFrames {
                readIndex = (readIndex + 1) % capacityFrames
                bufferedFrames -= 1
                dropped += 1
            }
            storage[writeIndex] = sample
            writeIndex = (writeIndex + 1) % capacityFrames
            bufferedFrames += 1
        }
        return dropped
    }

    mutating func render(count: Int) -> (samples: [Float], underrun: Bool) {
        var output = [Float](repeating: 0, count: count)
        var underrun = false
        for index in 0..<count {
            if bufferedFrames == 0 {
                underrun = true
                break
            }
            output[index] = storage[readIndex]
            readIndex = (readIndex + 1) % capacityFrames
            bufferedFrames -= 1
        }
        return (output, underrun)
    }

    mutating func reset() {
        readIndex = 0
        writeIndex = 0
        bufferedFrames = 0
    }
}

private extension FixedWidthInteger {
    var littleEndianData: Data {
        var value = littleEndian
        return withUnsafeBytes(of: &value) { Data($0) }
    }
}

/// 实验错误。
enum PlaybackExperimentError: LocalizedError {
    case permissionDenied(String)
    case audio(String)
    case network(String)

    var errorDescription: String? {
        switch self {
        case let .permissionDenied(message), let .audio(message), let .network(message):
            return message
        }
    }
}
