import AVFoundation
import Foundation

/// 单步音频探针类型。
///
/// 主要功能：把完整实验拆成可单独点击的系统音频 API，定位真机卡死发生在哪一步。
enum AudioProbeStep: String, CaseIterable, Identifiable {
    case microphonePermission
    case configureSession
    case voiceProcessingOn
    case voiceProcessingOff
    case readInputFormat
    case startEngine
    case stopEngine
    case deactivateSession

    var id: String { rawValue }

    var title: String {
        switch self {
        case .microphonePermission:
            return "麦克风权限"
        case .configureSession:
            return "配置音频会话"
        case .voiceProcessingOn:
            return "VoiceProcessing 开"
        case .voiceProcessingOff:
            return "VoiceProcessing 关"
        case .readInputFormat:
            return "读取 input format"
        case .startEngine:
            return "启动 engine"
        case .stopEngine:
            return "停止 engine"
        case .deactivateSession:
            return "释放音频会话"
        }
    }
}

/// 单步音频探针。
///
/// 主要功能：持有同一个 AVAudioEngine，让用户按按钮逐步执行音频配置、Voice Processing 和 engine 启停。
final class AudioStepProbe: @unchecked Sendable {
    private let engine = AVAudioEngine()

    /// 执行一个单步探针。
    ///
    /// 主要逻辑：只运行 `step` 对应的一小段系统音频 API，返回当前观察到的状态摘要。
    /// 参数：`step` 是单步操作。
    /// 返回值：路由或音频格式摘要。
    /// 异常情况：系统音频 API 抛错时继续向上抛出。
    func run(_ step: AudioProbeStep) throws -> String {
        switch step {
        case .microphonePermission:
            try requestMicrophonePermission()
            return "permission=granted"
        case .configureSession:
            return try configureSession()
        case .voiceProcessingOn:
            try engine.inputNode.setVoiceProcessingEnabled(true)
            return inputSummary(prefix: "voice_processing=on")
        case .voiceProcessingOff:
            try engine.inputNode.setVoiceProcessingEnabled(false)
            return inputSummary(prefix: "voice_processing=off")
        case .readInputFormat:
            return inputSummary(prefix: "format")
        case .startEngine:
            try engine.start()
            return inputSummary(prefix: "engine=running")
        case .stopEngine:
            engine.stop()
            return "engine=stopped"
        case .deactivateSession:
            engine.stop()
            try AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
            return "session=inactive"
        }
    }

    private func requestMicrophonePermission() throws {
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .audio) { ok in
            granted = ok
            semaphore.signal()
        }
        semaphore.wait()
        guard granted else {
            throw AECExperimentError.permissionDenied("麦克风权限被拒绝")
        }
    }

    private func configureSession() throws -> String {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(
            .playAndRecord,
            mode: .voiceChat,
            options: [.defaultToSpeaker, .allowBluetoothHFP]
        )
        try session.setPreferredSampleRate(16_000)
        try session.setPreferredIOBufferDuration(0.02)
        try session.setActive(true)
        return Self.routeSummary(session.currentRoute)
    }

    private func inputSummary(prefix: String) -> String {
        let format = engine.inputNode.outputFormat(forBus: 0)
        return "\(prefix) sample_rate=\(Int(format.sampleRate)) channels=\(format.channelCount)"
    }

    private static func routeSummary(_ route: AVAudioSessionRouteDescription) -> String {
        let inputs = route.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        let outputs = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        return "inputs[\(inputs)] outputs[\(outputs)]"
    }
}

/// AEC/VAD 单次实验结果。
///
/// 主要功能：记录本次实验的开关状态、本地 WAV、音频路由和独立 VAD 分析结果。
struct AECExperimentResult {
    let voiceProcessingEnabled: Bool
    let wavURL: URL
    let vadTriggered: Bool?
    let vadSummary: String
    let routeSummary: String
}

/// AEC/VAD 单次实验运行器。
///
/// 主要功能：不连接 agent-server，不依赖设备 SDK，只验证 iOS Voice Processing 处理后的麦克风音频
/// 是否仍会被独立 VAD 服务识别为语音。
final class AECExperimentRunner {
    private let engine = AVAudioEngine()
    private var player: AVAudioPlayer?

    /// 执行一次完整实验。
    ///
    /// 主要逻辑：申请麦克风权限，配置 playAndRecord + voiceChat，按参数开关 input voice processing；
    /// 然后录制处理后的 input tap，同时用系统 TTS 外放固定文本，最后把生成的 WAV 上传到 VAD。
    /// 参数：`voiceProcessingEnabled` 控制 Voice Processing；`vadURL` 为空时只生成 WAV；`progress` 回传阶段日志。
    /// 返回值：实验结果。
    /// 异常情况：权限、音频配置、文件写入或 VAD 请求失败时抛出错误。
    func run(
        voiceProcessingEnabled: Bool,
        vadURL: URL?,
        progress: @escaping @Sendable (String) -> Void
    ) throws -> AECExperimentResult {
        progress("请求麦克风权限")
        try requestMicrophonePermission()

        progress("配置音频会话")
        let routeSummary = try configureAudio(voiceProcessingEnabled: voiceProcessingEnabled, progress: progress)
        let wavURL = makeWAVURL(voiceProcessingEnabled: voiceProcessingEnabled)
        progress("加载内置测试音频")
        let probeURL = try bundledProbeWAV()

        progress("安装麦克风 tap")
        let recorder = try installTap()
        defer {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            player?.stop()
            player = nil
        }

        progress("启动音频引擎")
        try engine.start()

        Thread.sleep(forTimeInterval: 0.3)
        progress("播放测试音并录制 8 秒")
        try playProbe(url: probeURL)
        Thread.sleep(forTimeInterval: 8.0)
        player?.stop()
        Thread.sleep(forTimeInterval: 0.5)

        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        progress("离线写入录音 WAV")
        try writeCapturedWAV(recorder.drain(), to: wavURL, progress: progress)

        guard let vadURL else {
            return AECExperimentResult(
                voiceProcessingEnabled: voiceProcessingEnabled,
                wavURL: wavURL,
                vadTriggered: nil,
                vadSummary: "未配置 VAD 服务",
                routeSummary: routeSummary
            )
        }

        progress("上传 WAV 到 VAD")
        let vad = try postWAV(wavURL: wavURL, vadURL: vadURL)
        return AECExperimentResult(
            voiceProcessingEnabled: voiceProcessingEnabled,
            wavURL: wavURL,
            vadTriggered: vad.triggered,
            vadSummary: vad.summary,
            routeSummary: routeSummary
        )
    }

    private func requestMicrophonePermission() throws {
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        AVCaptureDevice.requestAccess(for: .audio) { ok in
            granted = ok
            semaphore.signal()
        }
        semaphore.wait()
        guard granted else {
            throw AECExperimentError.permissionDenied("麦克风权限被拒绝")
        }
    }

    private func configureAudio(
        voiceProcessingEnabled: Bool,
        progress: (String) -> Void
    ) throws -> String {
        let session = AVAudioSession.sharedInstance()
        progress("配置 setCategory")
        try session.setCategory(
            .playAndRecord,
            mode: .voiceChat,
            options: [.defaultToSpeaker, .allowBluetoothHFP]
        )
        progress("配置 preferredSampleRate")
        try session.setPreferredSampleRate(16_000)
        progress("配置 preferredIOBufferDuration")
        try session.setPreferredIOBufferDuration(0.02)
        progress("配置 setActive")
        try session.setActive(true)
        progress("配置 setVoiceProcessingEnabled")
        try engine.inputNode.setVoiceProcessingEnabled(voiceProcessingEnabled)
        progress("配置完成")
        return Self.routeSummary(session.currentRoute)
    }

    private func installTap() throws -> CapturedAudioRecorder {
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        let recorder = CapturedAudioRecorder(format: inputFormat)
        let bufferSize = AVAudioFrameCount(max(1, Int(inputFormat.sampleRate * 0.02)))

        input.installTap(onBus: 0, bufferSize: bufferSize, format: inputFormat) { buffer, _ in
            recorder.append(buffer)
        }

        return recorder
    }

    private func writeCapturedWAV(
        _ capture: CapturedAudio,
        to url: URL,
        progress: (String) -> Void
    ) throws {
        if FileManager.default.fileExists(atPath: url.path) {
            try FileManager.default.removeItem(at: url)
        }
        progress("离线转换 PCM buffers=\(capture.buffers.count) input_rate=\(Int(capture.format.sampleRate))")
        let pcm = makeMono16kPCM(capture)
        progress("离线写 WAV bytes=\(pcm.count)")
        try writeWAV(pcm: pcm, sampleRate: 16_000, channels: 1, to: url)
        progress("离线 WAV 完成")
    }

    private func makeMono16kPCM(_ capture: CapturedAudio) -> Data {
        let inputRate = max(capture.format.sampleRate, 1)
        let step = max(1, Int(round(inputRate / 16_000.0)))
        var pcm = Data()
        for buffer in capture.buffers {
            let frameLength = Int(buffer.frameLength)
            if let floatChannels = buffer.floatChannelData {
                let channelCount = max(1, Int(buffer.format.channelCount))
                for frame in stride(from: 0, to: frameLength, by: step) {
                    var sample = Float(0)
                    for channel in 0..<channelCount {
                        sample += floatChannels[channel][frame]
                    }
                    sample /= Float(channelCount)
                    appendInt16Sample(sample, to: &pcm)
                }
            } else if let int16Channels = buffer.int16ChannelData {
                let channelCount = max(1, Int(buffer.format.channelCount))
                for frame in stride(from: 0, to: frameLength, by: step) {
                    var mixed = Int(0)
                    for channel in 0..<channelCount {
                        mixed += Int(int16Channels[channel][frame])
                    }
                    var value = Int16(max(Int(Int16.min), min(Int(Int16.max), mixed / channelCount))).littleEndian
                    withUnsafeBytes(of: &value) { bytes in
                        pcm.append(contentsOf: bytes)
                    }
                }
            }
        }
        return pcm
    }

    private func appendInt16Sample(_ sample: Float, to data: inout Data) {
        let clamped = max(-1.0, min(1.0, sample))
        var value = Int16(clamped * Float(Int16.max)).littleEndian
        withUnsafeBytes(of: &value) { bytes in
            data.append(contentsOf: bytes)
        }
    }

    private func writeWAV(pcm: Data, sampleRate: UInt32, channels: UInt16, to url: URL) throws {
        var wav = Data()
        appendASCII("RIFF", to: &wav)
        appendUInt32LE(UInt32(36 + pcm.count), to: &wav)
        appendASCII("WAVE", to: &wav)
        appendASCII("fmt ", to: &wav)
        appendUInt32LE(16, to: &wav)
        appendUInt16LE(1, to: &wav)
        appendUInt16LE(channels, to: &wav)
        appendUInt32LE(sampleRate, to: &wav)
        appendUInt32LE(sampleRate * UInt32(channels) * 2, to: &wav)
        appendUInt16LE(channels * 2, to: &wav)
        appendUInt16LE(16, to: &wav)
        appendASCII("data", to: &wav)
        appendUInt32LE(UInt32(pcm.count), to: &wav)
        wav.append(pcm)
        try wav.write(to: url, options: [.atomic])
    }

    private func appendASCII(_ text: String, to data: inout Data) {
        data.append(contentsOf: text.utf8)
    }

    private func appendUInt16LE(_ value: UInt16, to data: inout Data) {
        var littleEndian = value.littleEndian
        withUnsafeBytes(of: &littleEndian) { bytes in
            data.append(contentsOf: bytes)
        }
    }

    private func appendUInt32LE(_ value: UInt32, to data: inout Data) {
        var littleEndian = value.littleEndian
        withUnsafeBytes(of: &littleEndian) { bytes in
            data.append(contentsOf: bytes)
        }
    }

    private func playProbe(url: URL) throws {
        let player = try AVAudioPlayer(contentsOf: url)
        player.numberOfLoops = -1
        player.volume = 1.0
        player.prepareToPlay()
        guard player.play() else {
            throw AECExperimentError.audio("测试音播放失败")
        }
        self.player = player
    }

    private func postWAV(wavURL: URL, vadURL: URL) throws -> (triggered: Bool, summary: String) {
        var request = URLRequest(url: vadURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 10
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.httpBody = try Data(contentsOf: wavURL)
        let semaphore = DispatchSemaphore(value: 0)
        var responseData: Data?
        var responseObject: URLResponse?
        var responseError: Error?
        URLSession.shared.dataTask(with: request) { data, response, error in
            responseData = data
            responseObject = response
            responseError = error
            semaphore.signal()
        }.resume()
        semaphore.wait()
        if let responseError {
            throw responseError
        }
        let data = responseData ?? Data()
        let response = responseObject
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let text = String(data: data, encoding: .utf8) ?? ""
            throw AECExperimentError.vad("VAD 请求失败：\(text)")
        }
        let object = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let triggered = object?["triggered"] as? Bool
        let speechFrames = object?["speech_frames"] as? Int ?? 0
        let totalFrames = object?["total_frames"] as? Int ?? 0
        let firstSpeechMS = object?["first_speech_ms"] as? Int
        let ratio = object?["speech_ratio"] as? Double ?? 0
        let summary = String(
            format: "triggered=%@ speech_frames=%d/%d ratio=%.3f first_speech_ms=%@",
            triggered == true ? "true" : "false",
            speechFrames,
            totalFrames,
            ratio,
            firstSpeechMS.map(String.init) ?? "-"
        )
        return (triggered == true, summary)
    }

    private func bundledProbeWAV() throws -> URL {
        guard let url = Bundle.main.url(forResource: "自我介绍一下", withExtension: "wav") else {
            throw AECExperimentError.audio("没有找到内置测试音频：自我介绍一下.wav")
        }
        return url
    }

    private func makeWAVURL(voiceProcessingEnabled: Bool) -> URL {
        let documentsURL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        let suffix = voiceProcessingEnabled ? "vp-on" : "vp-off"
        return documentsURL.appendingPathComponent("aec-vad-\(suffix)-\(Self.timestamp()).wav")
    }

    private static func routeSummary(_ route: AVAudioSessionRouteDescription) -> String {
        let inputs = route.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        let outputs = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        return "inputs[\(inputs)] outputs[\(outputs)]"
    }

    private static func timestamp() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter.string(from: Date())
    }
}

/// 录音 tap 捕获到的原始音频。
///
/// 主要功能：把实时音频回调中复制出来的 buffer 和原始格式一起交给离线写 WAV 阶段。
private struct CapturedAudio {
    let format: AVAudioFormat
    let buffers: [AVAudioPCMBuffer]
}

/// 轻量录音缓存。
///
/// 主要功能：tap 回调只复制 buffer 并入队，不做格式转换、不写文件，避免阻塞音频实时线程。
private final class CapturedAudioRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private let format: AVAudioFormat
    private var buffers: [AVAudioPCMBuffer] = []

    init(format: AVAudioFormat) {
        self.format = format
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else {
            return
        }
        copy.frameLength = buffer.frameLength
        if let source = buffer.floatChannelData, let target = copy.floatChannelData {
            let channelCount = Int(buffer.format.channelCount)
            let frameLength = Int(buffer.frameLength)
            for channel in 0..<channelCount {
                target[channel].assign(from: source[channel], count: frameLength)
            }
        } else if let source = buffer.int16ChannelData, let target = copy.int16ChannelData {
            let channelCount = Int(buffer.format.channelCount)
            let frameLength = Int(buffer.frameLength)
            for channel in 0..<channelCount {
                target[channel].assign(from: source[channel], count: frameLength)
            }
        } else {
            return
        }
        lock.lock()
        buffers.append(copy)
        lock.unlock()
    }

    func drain() -> CapturedAudio {
        lock.lock()
        let result = buffers
        buffers.removeAll()
        lock.unlock()
        return CapturedAudio(format: format, buffers: result)
    }
}

private enum AECExperimentError: LocalizedError {
    case permissionDenied(String)
    case audio(String)
    case vad(String)

    var errorDescription: String? {
        switch self {
        case let .permissionDenied(message), let .audio(message), let .vad(message):
            return message
        }
    }
}
