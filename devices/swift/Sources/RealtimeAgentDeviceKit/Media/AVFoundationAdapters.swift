import Foundation

#if canImport(AVFoundation)
@preconcurrency import AVFoundation

/// AVFoundation 默认麦克风 source。
///
/// 主要功能：使用系统麦克风采集音频，转换为 SDK 约定的 PCM16LE chunk。
public final class RealtimeAgentDefaultMicrophoneSource: RealtimeAgentMicrophoneSource, @unchecked Sendable {
    public init() {}

    public func streamPCM16LE(configuration: RealtimeAgentMicrophoneConfiguration) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            let session = RealtimeAgentMicrophoneCaptureSession(configuration: configuration, continuation: continuation)
            do {
                try session.start()
            } catch {
                continuation.finish(throwing: error)
            }
            continuation.onTermination = { _ in
                session.stop()
            }
        }
    }
}

private final class RealtimeAgentMicrophoneCaptureSession: @unchecked Sendable {
    private let configuration: RealtimeAgentMicrophoneConfiguration
    private let continuation: AsyncThrowingStream<Data, Error>.Continuation
    private let voiceEngine = RealtimeAgentVoiceConversationEngine.shared

    init(
        configuration: RealtimeAgentMicrophoneConfiguration,
        continuation: AsyncThrowingStream<Data, Error>.Continuation
    ) {
        self.configuration = configuration
        self.continuation = continuation
    }

    func start() throws {
        try voiceEngine.startMicrophone(configuration: configuration, continuation: continuation)
    }

    func stop() {
        voiceEngine.stopMicrophone()
    }
}

/// AVFoundation 默认相机 source。
///
/// 主要功能：使用系统摄像头拍摄 JPEG 单帧，供 SDK 响应 `sensor.rgb` 请求。
public final class RealtimeAgentDefaultCameraFrameSource: NSObject, RealtimeAgentCameraFrameSource, AVCapturePhotoCaptureDelegate, @unchecked Sendable {
    private let session = AVCaptureSession()
    private let output = AVCapturePhotoOutput()
    private let queue = DispatchQueue(label: "realtime-agent.default-camera")
    private var continuation: CheckedContinuation<Data, Error>?
    private var configured = false

    public override init() {}

    public func captureJPEG() async throws -> Data {
        try await configureIfNeeded()
        return try await withCheckedThrowingContinuation { continuation in
            queue.async {
                self.continuation = continuation
                let settings = AVCapturePhotoSettings()
                self.output.capturePhoto(with: settings, delegate: self)
            }
        }
    }

    private func configureIfNeeded() async throws {
        if configured { return }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            queue.async {
                do {
                    self.session.beginConfiguration()
                    self.session.sessionPreset = .photo
                    guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
                        ?? AVCaptureDevice.default(for: .video) else {
                        throw RealtimeAgentDeviceError.invalidStreamChunk("no camera device available")
                    }
                    let input = try AVCaptureDeviceInput(device: device)
                    if self.session.canAddInput(input) {
                        self.session.addInput(input)
                    }
                    if self.session.canAddOutput(self.output) {
                        self.session.addOutput(self.output)
                    }
                    self.session.commitConfiguration()
                    self.session.startRunning()
                    self.configured = true
                    continuation.resume()
                } catch {
                    self.session.commitConfiguration()
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    public func photoOutput(
        _: AVCapturePhotoOutput,
        didFinishProcessingPhoto photo: AVCapturePhoto,
        error: Error?
    ) {
        if let error {
            continuation?.resume(throwing: error)
        } else if let data = photo.fileDataRepresentation() {
            continuation?.resume(returning: data)
        } else {
            continuation?.resume(throwing: RealtimeAgentDeviceError.invalidStreamChunk("camera photo has no jpeg data"))
        }
        continuation = nil
    }
}

/// AVFoundation 默认 speaker sink。
///
/// 主要功能：把 SDK speaker buffer drain 出来的 PCM16LE chunk 写入本机播放器。
public final class RealtimeAgentDefaultSpeakerSink: RealtimeAgentSpeakerSink, @unchecked Sendable {
    private let voiceEngine = RealtimeAgentVoiceConversationEngine.shared

    public init() {}

    public func prepare(format: RealtimeAgentSpeakerFormat) async throws {
        try voiceEngine.prepareSpeaker(format: format)
    }

    public func write(_ chunk: RealtimeAgentStreamChunk) async throws {
        try await voiceEngine.writeSpeakerChunk(chunk)
    }

    public func drain() async throws {
        try await voiceEngine.drainSpeaker()
    }

    public func cancel() async {
        await voiceEngine.cancelSpeaker()
    }
}

/// iOS 默认实时语音音频引擎。
///
/// 主要功能：让 SDK 默认麦克风和 speaker 共用同一个 `AVAudioEngine`，并在同一条
/// Voice Processing I/O 路径里启用系统回声消除，避免端侧外放被本机麦克风再次采集。
private final class RealtimeAgentVoiceConversationEngine: @unchecked Sendable {
    static let shared = RealtimeAgentVoiceConversationEngine()

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let lock = NSRecursiveLock()
    private var microphoneConverter: AVAudioConverter?
    private var microphoneTargetFormat: AVAudioFormat?
    private var microphoneContinuation: AsyncThrowingStream<Data, Error>.Continuation?
    private var microphoneTapInstalled = false
    private var voiceProcessingEnabled = false
    private var speakerFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false)!
    private var preparedSpeakerFormat: RealtimeAgentSpeakerFormat?
    private var pendingPlaybackBuffers = 0
    private var pendingPlaybackMS = 0
    private static let drainPollNanoseconds: UInt64 = 20_000_000
    private static let drainGraceNanoseconds: UInt64 = 3_000_000_000

    private init() {}

    func startMicrophone(
        configuration: RealtimeAgentMicrophoneConfiguration,
        continuation: AsyncThrowingStream<Data, Error>.Continuation
    ) throws {
        lock.lock()
        defer { lock.unlock() }

        try configureAudioSession(sampleRate: Double(configuration.sampleRate))
        try configureVoiceProcessingIfNeeded()
        if microphoneTapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            microphoneTapInstalled = false
        }

        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(configuration.sampleRate),
            channels: AVAudioChannelCount(configuration.channels),
            interleaved: false
        ) else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("cannot create target microphone format")
        }
        microphoneTargetFormat = targetFormat
        if inputFormat.sampleRate != targetFormat.sampleRate || inputFormat.channelCount != targetFormat.channelCount {
            microphoneConverter = AVAudioConverter(from: inputFormat, to: targetFormat)
        } else {
            microphoneConverter = nil
        }
        microphoneContinuation = continuation

        let bufferSize = AVAudioFrameCount(max(1, configuration.sampleRate * configuration.chunkMS / 1000))
        input.installTap(onBus: 0, bufferSize: bufferSize, format: inputFormat) { [weak self] buffer, _ in
            guard let self else { return }
            do {
                let pcm = try self.convertMicrophoneBuffer(buffer, inputFormat: inputFormat, targetFormat: targetFormat)
                self.microphoneContinuation?.yield(pcm)
            } catch {
                self.microphoneContinuation?.finish(throwing: error)
            }
        }
        microphoneTapInstalled = true
        try startEngineIfNeeded()
    }

    func stopMicrophone() {
        lock.lock()
        defer { lock.unlock() }
        if microphoneTapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            microphoneTapInstalled = false
        }
        microphoneContinuation = nil
        microphoneConverter = nil
        microphoneTargetFormat = nil
        stopEngineIfIdle()
    }

    func prepareSpeaker(format: RealtimeAgentSpeakerFormat) throws {
        lock.lock()
        defer { lock.unlock() }

        if isPreparedForCurrentPlayback(format) {
            return
        }
        guard format.codec == "pcm16le" else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("unsupported speaker codec: \(format.codec)")
        }
        try configureAudioSession(sampleRate: Double(format.sampleRate))
        try configureVoiceProcessingIfNeeded()
        guard let playbackFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(format.sampleRate),
            channels: AVAudioChannelCount(format.channels),
            interleaved: false
        ) else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("cannot create speaker format")
        }
        speakerFormat = playbackFormat
        if !engine.attachedNodes.contains(player) {
            engine.attach(player)
        }
        engine.connect(player, to: engine.mainMixerNode, format: playbackFormat)
        try startEngineIfNeeded()
        if !player.isPlaying {
            player.play()
        }
        preparedSpeakerFormat = format
    }

    func writeSpeakerChunk(_ chunk: RealtimeAgentStreamChunk) async throws {
        guard let buffer = pcmBuffer(from: chunk.payload, format: currentSpeakerFormat()) else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("cannot create speaker pcm buffer")
        }
        incrementPendingPlaybackBuffers(durationMS: chunk.durationMS)
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            self?.markPlaybackBufferFinished(durationMS: chunk.durationMS)
        }
    }

    func drainSpeaker() async throws {
        try await waitUntilPlaybackQueueEmpty()
    }

    func cancelSpeaker() async {
        cancelSpeakerNow()
    }

    private func cancelSpeakerNow() {
        lock.lock()
        defer { lock.unlock() }
        player.stop()
        preparedSpeakerFormat = nil
        pendingPlaybackBuffers = 0
        pendingPlaybackMS = 0
        stopEngineIfIdle()
    }

    private func isPreparedForCurrentPlayback(_ format: RealtimeAgentSpeakerFormat) -> Bool {
        let prepared = preparedSpeakerFormat == format
        guard prepared else {
            return false
        }
        if !engine.isRunning {
            return false
        }
        if !player.isPlaying {
            player.play()
        }
        return true
    }

    private func configureAudioSession(sampleRate: Double) throws {
        #if os(iOS) || os(tvOS) || os(visionOS)
        try RealtimeAgentAudioSession.configureVoiceConversation(sampleRate: sampleRate)
        #endif
    }

    private func configureVoiceProcessingIfNeeded() throws {
        guard !voiceProcessingEnabled else { return }
        if engine.isRunning {
            engine.stop()
        }
        #if os(iOS) || os(tvOS) || os(visionOS)
        try RealtimeAgentAudioSession.enableVoiceProcessing(on: engine.inputNode, role: "microphone")
        try RealtimeAgentAudioSession.enableVoiceProcessing(on: engine.outputNode, role: "speaker")
        #endif
        voiceProcessingEnabled = true
    }

    private func startEngineIfNeeded() throws {
        if !engine.attachedNodes.contains(player) {
            engine.attach(player)
        }
        engine.prepare()
        if !engine.isRunning {
            try engine.start()
        }
    }

    private func stopEngineIfIdle() {
        if !microphoneTapInstalled && preparedSpeakerFormat == nil && pendingPlaybackBuffers == 0 {
            engine.stop()
            voiceProcessingEnabled = false
            #if os(iOS) || os(tvOS) || os(visionOS)
            try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
            #endif
        }
    }

    private func currentSpeakerFormat() -> AVAudioFormat {
        lock.lock()
        defer { lock.unlock() }
        return speakerFormat
    }

    private func convertMicrophoneBuffer(
        _ buffer: AVAudioPCMBuffer,
        inputFormat: AVAudioFormat,
        targetFormat: AVAudioFormat
    ) throws -> Data {
        lock.lock()
        let converter = microphoneConverter
        lock.unlock()

        let outputBuffer: AVAudioPCMBuffer
        if let converter {
            let ratio = targetFormat.sampleRate / max(inputFormat.sampleRate, 1)
            let capacity = AVAudioFrameCount(max(1, Double(buffer.frameLength) * ratio + 8))
            guard let converted = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else {
                throw RealtimeAgentDeviceError.invalidStreamChunk("cannot allocate converted microphone buffer")
            }
            var conversionError: NSError?
            let inputProvider = RealtimeAgentAudioConverterInput(buffer: buffer)
            let status = converter.convert(to: converted, error: &conversionError) { _, outStatus in
                inputProvider.next(outStatus: outStatus)
            }
            if let conversionError {
                throw conversionError
            }
            guard status != .error else {
                throw RealtimeAgentDeviceError.invalidStreamChunk("microphone conversion failed")
            }
            outputBuffer = converted
        } else {
            outputBuffer = buffer
        }
        return AudioPCMConverter.pcm16LE(fromFloat32: monoSamples(from: outputBuffer))
    }

    private func monoSamples(from buffer: AVAudioPCMBuffer) -> [Float] {
        guard let channels = buffer.floatChannelData else { return [] }
        let frameCount = Int(buffer.frameLength)
        let channelCount = max(1, Int(buffer.format.channelCount))
        if channelCount == 1 {
            return Array(UnsafeBufferPointer(start: channels[0], count: frameCount))
        }
        var samples = [Float](repeating: 0, count: frameCount)
        for channelIndex in 0..<channelCount {
            let channel = UnsafeBufferPointer(start: channels[channelIndex], count: frameCount)
            for index in 0..<frameCount {
                samples[index] += channel[index] / Float(channelCount)
            }
        }
        return samples
    }

    private func incrementPendingPlaybackBuffers(durationMS: Int) {
        lock.lock()
        pendingPlaybackBuffers += 1
        pendingPlaybackMS += max(durationMS, 0)
        lock.unlock()
    }

    private func markPlaybackBufferFinished(durationMS: Int) {
        lock.lock()
        pendingPlaybackBuffers = max(0, pendingPlaybackBuffers - 1)
        pendingPlaybackMS = max(0, pendingPlaybackMS - max(durationMS, 0))
        stopEngineIfIdle()
        lock.unlock()
    }

    private func waitUntilPlaybackQueueEmpty() async throws {
        let timeoutNanoseconds = playbackDrainTimeoutNanoseconds()
        let startedAt = DispatchTime.now().uptimeNanoseconds
        while true {
            let pending = pendingPlaybackSnapshot()
            if pending.buffers == 0 {
                return
            }
            let elapsed = DispatchTime.now().uptimeNanoseconds - startedAt
            if elapsed >= timeoutNanoseconds {
                throw RealtimeAgentDeviceError.transportClosed(
                    "speaker playback drain timeout: pending_buffers=\(pending.buffers) pending_ms=\(pending.ms)"
                )
            }
            try await Task.sleep(nanoseconds: Self.drainPollNanoseconds)
        }
    }

    private func pendingPlaybackSnapshot() -> (buffers: Int, ms: Int) {
        lock.lock()
        let snapshot = (pendingPlaybackBuffers, pendingPlaybackMS)
        lock.unlock()
        return snapshot
    }

    private func playbackDrainTimeoutNanoseconds() -> UInt64 {
        let pendingMS = pendingPlaybackSnapshot().ms
        let pendingNanoseconds = UInt64(max(pendingMS, 0)) * 1_000_000
        return pendingNanoseconds + Self.drainGraceNanoseconds
    }

    private func pcmBuffer(from data: Data, format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let sampleCount = data.count / 2
        guard sampleCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sampleCount)) else {
            return nil
        }
        buffer.frameLength = AVAudioFrameCount(sampleCount)
        guard let channel = buffer.floatChannelData?[0] else {
            return nil
        }
        data.withUnsafeBytes { rawBuffer in
            guard let bytes = rawBuffer.bindMemory(to: UInt8.self).baseAddress else { return }
            for index in 0..<sampleCount {
                let low = UInt16(bytes[index * 2])
                let high = UInt16(bytes[index * 2 + 1]) << 8
                let value = Int16(bitPattern: high | low)
                channel[index] = Float(value) / 32768.0
            }
        }
        return buffer
    }
}

#if os(iOS) || os(tvOS) || os(visionOS)
private enum RealtimeAgentAudioSession {
    /// 配置适合实时语音对话的系统音频会话。
    ///
    /// 主要逻辑：使用 `.voiceChat` 让系统启用语音处理链路，包括回声抑制、自动增益和噪声处理；
    /// 默认走扬声器，避免端侧示例 App 每次麦克风或 speaker 准备时互相覆盖为普通播放模式。
    static func configureVoiceConversation(sampleRate: Double) throws {
        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(
            .playAndRecord,
            mode: .voiceChat,
            options: [.defaultToSpeaker, .allowBluetooth]
        )
        try audioSession.setPreferredSampleRate(sampleRate)
        try audioSession.setActive(true)
    }

    /// 启用系统语音处理链路。
    ///
    /// 主要逻辑：让麦克风和扬声器都进入 AVAudioEngine 的 voice-processing I/O 路径，
    /// 这样系统回声消除器可以获得播放参考信号，降低端侧播报被本机麦克风再次采集后触发误打断的概率。
    static func enableVoiceProcessing(on node: AVAudioIONode, role: String) throws {
        do {
            try node.setVoiceProcessingEnabled(true)
        } catch {
            throw RealtimeAgentDeviceError.transportClosed(
                "cannot enable \(role) voice processing: \(error.localizedDescription)"
            )
        }
    }
}
#endif

private final class RealtimeAgentAudioConverterInput: @unchecked Sendable {
    private let buffer: AVAudioPCMBuffer
    private let lock = NSLock()
    private var didProvideInput = false

    init(buffer: AVAudioPCMBuffer) {
        self.buffer = buffer
    }

    func next(outStatus: UnsafeMutablePointer<AVAudioConverterInputStatus>) -> AVAudioBuffer? {
        lock.lock()
        defer { lock.unlock() }
        if didProvideInput {
            outStatus.pointee = .noDataNow
            return nil
        }
        didProvideInput = true
        outStatus.pointee = .haveData
        return buffer
    }
}
#endif
