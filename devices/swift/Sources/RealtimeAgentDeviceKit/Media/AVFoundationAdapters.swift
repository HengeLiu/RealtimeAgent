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
public final class RealtimeAgentDefaultSpeakerSink: RealtimeAgentSpeakerSink, RealtimeAgentSpeakerSinkDiagnostics, @unchecked Sendable {
    private let voiceEngine = RealtimeAgentVoiceConversationEngine.shared
    private let lock = NSRecursiveLock()
    private let engine = AVAudioEngine()
    private let ringBuffer = RealtimeAgentFloatRingBuffer(
        capacityFrames: 24000 * 30,
        startThresholdFrames: 24000 * 300 / 1000
    )
    private var format = RealtimeAgentSpeakerFormat()
    private var sourceNode: AVAudioSourceNode?
    private var playbackFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24_000, channels: 1, interleaved: false)!
    private var preparedRingFormat: RealtimeAgentSpeakerFormat?
    private var preparedSessionSampleRate: Int?
    private var lastPrepareDiagnostics = "ring_prepare=none"

    public init() {}

    public func prepare(format: RealtimeAgentSpeakerFormat) async throws {
        let startedAt = DispatchTime.now().uptimeNanoseconds
        guard format.codec == "pcm16le" else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("unsupported speaker codec: \(format.codec)")
        }
        let shouldConfigureSession = shouldConfigureAudioSession(sampleRate: format.sampleRate)
        let configureStartedAt = DispatchTime.now().uptimeNanoseconds
        #if os(iOS) || os(tvOS) || os(visionOS)
        if shouldConfigureSession {
            try RealtimeAgentAudioSession.configureVoiceConversation(sampleRate: Double(format.sampleRate))
        }
        #endif
        let configuredAt = DispatchTime.now().uptimeNanoseconds
        try resetRingPlayerState(
            format: format,
            diagnostics: "ring_prepare=completed elapsed_ms=\(elapsedMS(startedAt, DispatchTime.now().uptimeNanoseconds)) configure_session_ms=\(elapsedMS(configureStartedAt, configuredAt)) session_reused=\(!shouldConfigureSession)"
        )
    }

    public func write(_ chunk: RealtimeAgentStreamChunk) async throws {
        let samples = floatSamples(fromPCM16LE: chunk.payload)
        appendSamples(samples)
    }

    public func drain() async throws {
        voiceEngine.setExternalSpeakerPlaybackActive(true)
        ringBuffer.forceStart()
        while ringBuffer.bufferedFrames > 0 {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        try await Task.sleep(nanoseconds: 120_000_000)
        voiceEngine.setExternalSpeakerPlaybackActive(false)
    }

    public func cancel() async {
        cancelRingPlayerState()
        voiceEngine.setExternalSpeakerPlaybackActive(false)
    }

    public func diagnosticSummary() async -> String {
        "\(ringPlayerDiagnosticSummary()) \(voiceEngine.diagnosticSummary())"
    }

    private func elapsedMS(_ start: UInt64, _ end: UInt64) -> Int {
        Int((end - start) / 1_000_000)
    }

    private func shouldConfigureAudioSession(sampleRate: Int) -> Bool {
        lock.lock()
        let shouldConfigure = preparedSessionSampleRate != sampleRate
        if shouldConfigure {
            preparedSessionSampleRate = sampleRate
        }
        lock.unlock()
        return shouldConfigure
    }

    private func resetRingPlayerState(format: RealtimeAgentSpeakerFormat, diagnostics: String) throws {
        lock.lock()
        defer { lock.unlock() }
        self.format = format
        ringBuffer.reset()
        if preparedRingFormat == format, sourceNode != nil {
            if !engine.isRunning {
                try engine.start()
            }
            lastPrepareDiagnostics = "ring_prepare=reused \(diagnostics)"
            return
        }
        engine.stop()
        if let sourceNode {
            engine.detach(sourceNode)
        }
        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(format.sampleRate),
            channels: AVAudioChannelCount(format.channels),
            interleaved: false
        ) else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("cannot create ring speaker format")
        }
        playbackFormat = outputFormat
        let node = AVAudioSourceNode { [weak self] _, _, frameCount, audioBufferList -> OSStatus in
            self?.renderRingBuffer(frameCount: frameCount, audioBufferList: audioBufferList)
            return noErr
        }
        sourceNode = node
        if !engine.attachedNodes.contains(node) {
            engine.attach(node)
        }
        engine.connect(node, to: engine.mainMixerNode, format: outputFormat)
        engine.prepare()
        if !engine.isRunning {
            try engine.start()
        }
        preparedRingFormat = format
        lastPrepareDiagnostics = diagnostics
    }

    private func appendSamples(_ samples: [Float]) {
        if !samples.isEmpty {
            ringBuffer.append(samples)
            voiceEngine.setExternalSpeakerPlaybackActive(true)
        }
    }

    private func cancelRingPlayerState() {
        ringBuffer.reset()
        lock.lock()
        engine.stop()
        if let sourceNode {
            engine.detach(sourceNode)
        }
        sourceNode = nil
        preparedRingFormat = nil
        preparedSessionSampleRate = nil
        lock.unlock()
    }

    private func ringPlayerDiagnosticSummary() -> String {
        "ring_player=true ring_buffered_frames=\(ringBuffer.bufferedFrames) ring_capacity_frames=\(ringBuffer.capacityFrames) ring_start_threshold_frames=\(ringBuffer.startThresholdFrames) ring_playback_started=\(ringBuffer.playbackStarted) ring_dropped_frames=\(ringBuffer.droppedFrames) ring_underrun_events=\(ringBuffer.underrunEvents) ring_underrun_frames=\(ringBuffer.underrunFrames) ring_warmup_zero_frames=\(ringBuffer.warmupZeroFrames) ring_engine_running=\(engine.isRunning) ring_sample_rate=\(Int(playbackFormat.sampleRate)) \(lastPrepareDiagnostics)"
    }

    private func renderRingBuffer(frameCount: AVAudioFrameCount, audioBufferList: UnsafeMutablePointer<AudioBufferList>) {
        let rendered = ringBuffer.render(count: Int(frameCount))
        let buffers = UnsafeMutableAudioBufferListPointer(audioBufferList)
        for bufferIndex in buffers.indices {
            guard let pointer = buffers[bufferIndex].mData?.assumingMemoryBound(to: Float.self) else {
                continue
            }
            for frameIndex in 0..<Int(frameCount) {
                pointer[frameIndex] = frameIndex < rendered.count ? rendered[frameIndex] : 0
            }
            buffers[bufferIndex].mDataByteSize = UInt32(Int(frameCount) * MemoryLayout<Float>.size)
        }
    }

    private func floatSamples(fromPCM16LE data: Data) -> [Float] {
        let sampleCount = data.count / 2
        guard sampleCount > 0 else { return [] }
        return data.withUnsafeBytes { rawBuffer in
            guard let bytes = rawBuffer.bindMemory(to: UInt8.self).baseAddress else { return [] }
            var samples = [Float]()
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

/// iOS 默认实时语音音频引擎。
///
/// 主要功能：让 SDK 默认麦克风和 speaker 共用同一个 `AVAudioEngine`，并在麦克风输入侧
/// 启用系统语音处理；播放期间使用静音 PCM 抑制本机外放被麦克风再次采集。
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
    private var externalSpeakerPlaybackActive = false
    private var externalSpeakerPlaybackStartedAt: UInt64?
    private var microphoneDuringSpeakerPlayback: MicrophoneDuringSpeakerPlayback = .allowInterruptions
    private var speakerPlaybackWarmupMuteMS = 500
    private var lastSpeakerPrepareDiagnostics = "prepare=none"
    private var lastAudioSessionNotification = "audio_session_notification=none"
    private var audioSessionObserversInstalled = false
    private var audioSessionObserverTokens: [NSObjectProtocol] = []
    private static let drainPollNanoseconds: UInt64 = 20_000_000
    private static let drainGraceNanoseconds: UInt64 = 3_000_000_000

    private init() {
        installAudioSessionObserversIfNeeded()
    }

    func startMicrophone(
        configuration: RealtimeAgentMicrophoneConfiguration,
        continuation: AsyncThrowingStream<Data, Error>.Continuation
    ) throws {
        lock.lock()
        defer { lock.unlock() }

        microphoneDuringSpeakerPlayback = configuration.microphoneDuringSpeakerPlayback
        speakerPlaybackWarmupMuteMS = configuration.speakerPlaybackWarmupMuteMS
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
            lastSpeakerPrepareDiagnostics = "prepare=reused"
            return
        }
        guard format.codec == "pcm16le" else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("unsupported speaker codec: \(format.codec)")
        }
        let startedAt = DispatchTime.now().uptimeNanoseconds
        let configureStartedAt = startedAt
        try configureAudioSession(sampleRate: Double(format.sampleRate))
        let configuredAt = DispatchTime.now().uptimeNanoseconds
        try configureVoiceProcessingIfNeeded()
        let voiceProcessedAt = DispatchTime.now().uptimeNanoseconds
        guard let playbackFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(format.sampleRate),
            channels: AVAudioChannelCount(format.channels),
            interleaved: false
        ) else {
            throw RealtimeAgentDeviceError.invalidStreamChunk("cannot create speaker format")
        }
        let formatCreatedAt = DispatchTime.now().uptimeNanoseconds
        speakerFormat = playbackFormat
        if !engine.attachedNodes.contains(player) {
            engine.attach(player)
        }
        engine.connect(player, to: engine.mainMixerNode, format: playbackFormat)
        let connectedAt = DispatchTime.now().uptimeNanoseconds
        try startEngineIfNeeded()
        let engineStartedAt = DispatchTime.now().uptimeNanoseconds
        if !player.isPlaying {
            player.play()
        }
        let playerStartedAt = DispatchTime.now().uptimeNanoseconds
        preparedSpeakerFormat = format
        lastSpeakerPrepareDiagnostics = [
            "prepare=completed",
            "total_ms=\(elapsedMS(startedAt, playerStartedAt))",
            "configure_session_ms=\(elapsedMS(configureStartedAt, configuredAt))",
            "voice_processing_ms=\(elapsedMS(configuredAt, voiceProcessedAt))",
            "format_ms=\(elapsedMS(voiceProcessedAt, formatCreatedAt))",
            "connect_ms=\(elapsedMS(formatCreatedAt, connectedAt))",
            "engine_start_ms=\(elapsedMS(connectedAt, engineStartedAt))",
            "player_play_ms=\(elapsedMS(engineStartedAt, playerStartedAt))",
        ].joined(separator: " ")
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

    func diagnosticSummary() -> String {
        lock.lock()
        defer { lock.unlock() }
        return diagnosticSummaryLocked()
    }

    func setExternalSpeakerPlaybackActive(_ active: Bool) {
        lock.lock()
        if active, externalSpeakerPlaybackStartedAt == nil {
            externalSpeakerPlaybackStartedAt = DispatchTime.now().uptimeNanoseconds
        }
        if !active {
            externalSpeakerPlaybackStartedAt = nil
        }
        externalSpeakerPlaybackActive = active
        lock.unlock()
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
        #endif
        voiceProcessingEnabled = true
    }

    private func installAudioSessionObserversIfNeeded() {
        #if os(iOS) || os(tvOS) || os(visionOS)
        guard !audioSessionObserversInstalled else { return }
        audioSessionObserversInstalled = true
        let center = NotificationCenter.default
        let names: [Notification.Name] = [
            AVAudioSession.routeChangeNotification,
            AVAudioSession.interruptionNotification,
            AVAudioSession.mediaServicesWereResetNotification,
        ]
        audioSessionObserverTokens = names.map { name in
            center.addObserver(forName: name, object: nil, queue: nil) { [weak self] notification in
                self?.recordAudioSessionNotification(notification)
            }
        }
        #endif
    }

    private func recordAudioSessionNotification(_ notification: Notification) {
        lock.lock()
        defer { lock.unlock() }
        #if os(iOS) || os(tvOS) || os(visionOS)
        lastAudioSessionNotification = RealtimeAgentAudioSession.notificationSummary(notification)
        #else
        lastAudioSessionNotification = "audio_session_notification=\(notification.name.rawValue)"
        #endif
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
        if shouldMuteMicrophoneForSpeakerPlayback() {
            return Data(repeating: 0, count: Int(outputBuffer.frameLength) * 2)
        }
        return AudioPCMConverter.pcm16LE(fromFloat32: monoSamples(from: outputBuffer))
    }

    private func shouldMuteMicrophoneForSpeakerPlayback() -> Bool {
        lock.lock()
        let playbackActive = externalSpeakerPlaybackActive || pendingPlaybackMS > 0 || pendingPlaybackBuffers > 0
        let shouldMute: Bool
        if microphoneDuringSpeakerPlayback == .muteDuringSpeakerPlayback {
            shouldMute = playbackActive
        } else {
            shouldMute = playbackActive
                && speakerPlaybackWarmupMuteMS > 0
                && isWithinSpeakerWarmupMuteWindowLocked()
                && isBuiltInSpeakerRoute()
        }
        lock.unlock()
        return shouldMute
    }

    private func isWithinSpeakerWarmupMuteWindowLocked() -> Bool {
        guard let startedAt = externalSpeakerPlaybackStartedAt else {
            return false
        }
        let elapsedMS = Int((DispatchTime.now().uptimeNanoseconds - startedAt) / 1_000_000)
        return elapsedMS < speakerPlaybackWarmupMuteMS
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

    private func diagnosticSummaryLocked() -> String {
        let renderSummary: String
        if engine.attachedNodes.contains(player),
           let nodeTime = player.lastRenderTime,
           let playerTime = player.playerTime(forNodeTime: nodeTime) {
            let sampleRate = max(playerTime.sampleRate, 1)
            let renderedMS = Int((Double(playerTime.sampleTime) / sampleRate) * 1000)
            renderSummary = "rendered_ms=\(renderedMS) render_sample_rate=\(Int(sampleRate))"
        } else {
            renderSummary = "rendered_ms=unknown render_sample_rate=unknown"
        }
        #if os(iOS) || os(tvOS) || os(visionOS)
        let sessionSummary = RealtimeAgentAudioSession.currentDiagnosticSummary()
        #else
        let sessionSummary = "audio_session=unavailable"
        #endif
        return [
            sessionSummary,
            "engine_running=\(engine.isRunning)",
            "player_playing=\(player.isPlaying)",
            "voice_processing_enabled=\(voiceProcessingEnabled)",
            "external_speaker_playback_active=\(externalSpeakerPlaybackActive)",
            "microphone_during_speaker_playback=\(microphoneDuringSpeakerPlayback.rawValue)",
            "speaker_playback_warmup_mute_ms=\(speakerPlaybackWarmupMuteMS)",
            "speaker_playback_warmup_active=\(isWithinSpeakerWarmupMuteWindowLocked())",
            "mic_muted_by_speaker=\(shouldMuteMicrophoneForSpeakerPlaybackLocked())",
            "pending_buffers=\(pendingPlaybackBuffers)",
            "pending_ms=\(pendingPlaybackMS)",
            renderSummary,
            lastSpeakerPrepareDiagnostics,
            lastAudioSessionNotification,
        ].joined(separator: " ")
    }

    private func elapsedMS(_ start: UInt64, _ end: UInt64) -> Int {
        Int((end - start) / 1_000_000)
    }

    private func shouldMuteMicrophoneForSpeakerPlaybackLocked() -> Bool {
        let playbackActive = externalSpeakerPlaybackActive || pendingPlaybackMS > 0 || pendingPlaybackBuffers > 0
        if microphoneDuringSpeakerPlayback == .muteDuringSpeakerPlayback {
            return playbackActive
        }
        return playbackActive
            && speakerPlaybackWarmupMuteMS > 0
            && isWithinSpeakerWarmupMuteWindowLocked()
            && isBuiltInSpeakerRoute()
    }

    private func isBuiltInSpeakerRoute() -> Bool {
        #if os(iOS) || os(tvOS) || os(visionOS)
        AVAudioSession.sharedInstance().currentRoute.outputs.contains { output in
            output.portType == .builtInSpeaker
        }
        #else
        false
        #endif
    }
}

private final class RealtimeAgentFloatRingBuffer: @unchecked Sendable {
    let capacityFrames: Int
    let startThresholdFrames: Int
    private let lock = NSRecursiveLock()
    private var storage: [Float]
    private var readIndex = 0
    private var writeIndex = 0
    private var count = 0
    private(set) var playbackStarted = false
    private(set) var droppedFrames = 0
    private(set) var underrunEvents = 0
    private(set) var underrunFrames = 0
    private(set) var warmupZeroFrames = 0

    init(capacityFrames: Int, startThresholdFrames: Int) {
        self.capacityFrames = max(capacityFrames, 1)
        self.startThresholdFrames = max(startThresholdFrames, 1)
        self.storage = [Float](repeating: 0, count: self.capacityFrames)
    }

    var bufferedFrames: Int {
        lock.lock()
        let value = count
        lock.unlock()
        return value
    }

    func append(_ samples: [Float]) {
        lock.lock()
        defer { lock.unlock() }
        for sample in samples {
            if count == capacityFrames {
                readIndex = (readIndex + 1) % capacityFrames
                count -= 1
                droppedFrames += 1
            }
            storage[writeIndex] = sample
            writeIndex = (writeIndex + 1) % capacityFrames
            count += 1
        }
    }

    func render(count requestedCount: Int) -> [Float] {
        lock.lock()
        defer { lock.unlock() }
        let requestedCount = max(requestedCount, 0)
        guard requestedCount > 0 else { return [] }
        if !playbackStarted {
            guard count >= min(startThresholdFrames, capacityFrames) else {
                warmupZeroFrames += requestedCount
                return [Float](repeating: 0, count: requestedCount)
            }
            playbackStarted = true
        }
        let outputCount = min(requestedCount, count)
        var output = [Float](repeating: 0, count: requestedCount)
        for index in 0..<outputCount {
            output[index] = storage[readIndex]
            readIndex = (readIndex + 1) % capacityFrames
            count -= 1
        }
        if outputCount < requestedCount {
            underrunEvents += 1
            underrunFrames += requestedCount - outputCount
        }
        return output
    }

    func forceStart() {
        lock.lock()
        playbackStarted = true
        lock.unlock()
    }

    func reset() {
        lock.lock()
        readIndex = 0
        writeIndex = 0
        count = 0
        playbackStarted = false
        droppedFrames = 0
        underrunEvents = 0
        underrunFrames = 0
        warmupZeroFrames = 0
        lock.unlock()
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
    /// 主要逻辑：只在输入节点启用 voice processing，让系统输入侧完成回声抑制、自动增益和噪声处理。
    /// 扬声器输出仍走普通 player 节点，避免外放路由下输出节点语音处理影响播放稳定性。
    static func enableVoiceProcessing(on node: AVAudioIONode, role: String) throws {
        do {
            try node.setVoiceProcessingEnabled(true)
        } catch {
            throw RealtimeAgentDeviceError.transportClosed(
                "cannot enable \(role) voice processing: \(error.localizedDescription)"
            )
        }
    }

    static func currentDiagnosticSummary() -> String {
        let audioSession = AVAudioSession.sharedInstance()
        return [
            "category=\(audioSession.category.rawValue)",
            "mode=\(audioSession.mode.rawValue)",
            "sample_rate=\(Int(audioSession.sampleRate))",
            "preferred_sample_rate=\(Int(audioSession.preferredSampleRate))",
            "io_buffer_ms=\(Int(audioSession.ioBufferDuration * 1000))",
            "preferred_io_buffer_ms=\(Int(audioSession.preferredIOBufferDuration * 1000))",
            "input_latency_ms=\(Int(audioSession.inputLatency * 1000))",
            "output_latency_ms=\(Int(audioSession.outputLatency * 1000))",
            "route=\(routeSummary(audioSession.currentRoute))",
            "secondary_audio_silenced=\(audioSession.secondaryAudioShouldBeSilencedHint)",
        ].joined(separator: " ")
    }

    static func notificationSummary(_ notification: Notification) -> String {
        var fields = ["audio_session_notification=\(notification.name.rawValue)"]
        if notification.name == AVAudioSession.routeChangeNotification,
           let rawReason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
           let reason = AVAudioSession.RouteChangeReason(rawValue: rawReason) {
            fields.append("reason=\(routeChangeReasonName(reason))")
        }
        if notification.name == AVAudioSession.interruptionNotification,
           let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
           let type = AVAudioSession.InterruptionType(rawValue: rawType) {
            fields.append("interruption=\(type == .began ? "began" : "ended")")
        }
        fields.append(currentDiagnosticSummary())
        return fields.joined(separator: " ")
    }

    private static func routeSummary(_ route: AVAudioSessionRouteDescription) -> String {
        let inputs = route.inputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        let outputs = route.outputs.map { "\($0.portType.rawValue):\($0.portName)" }.joined(separator: ",")
        return "inputs[\(inputs.isEmpty ? "-" : inputs)] outputs[\(outputs.isEmpty ? "-" : outputs)]"
    }

    private static func routeChangeReasonName(_ reason: AVAudioSession.RouteChangeReason) -> String {
        switch reason {
        case .unknown: return "unknown"
        case .newDeviceAvailable: return "new_device_available"
        case .oldDeviceUnavailable: return "old_device_unavailable"
        case .categoryChange: return "category_change"
        case .override: return "override"
        case .wakeFromSleep: return "wake_from_sleep"
        case .noSuitableRouteForCategory: return "no_suitable_route"
        case .routeConfigurationChange: return "route_configuration_change"
        @unknown default: return "unknown_future"
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
