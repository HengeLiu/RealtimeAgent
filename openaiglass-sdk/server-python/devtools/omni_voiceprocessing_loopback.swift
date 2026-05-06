// macOS 原生 VoiceProcessingIO 到 DashScope Omni Realtime 的最小调试客户端。
//
// 这个文件故意保持为单文件 Swift 脚本，便于直接用 `swift` 运行，不引入 Xcode 工程。
// 它使用 AVAudioEngine 的 voice processing 输入/输出链路，让 macOS 系统处理回声抑制，
// 再把麦克风 PCM16 上行到 Omni，并把 Omni 返回的 PCM16 音频直接播放出来。

import AVFoundation
import Foundation

private let defaultModel = "qwen3.5-omni-plus-realtime"
private let defaultURL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
private let inputSampleRate: Double = 16_000
private let outputSampleRate: Double = 24_000
private let channelCount: AVAudioChannelCount = 1

private struct Config {
    var model = ProcessInfo.processInfo.environment["VOICE_OMNI_REALTIME_MODEL_NAME"] ?? defaultModel
    var url = ProcessInfo.processInfo.environment["VOICE_OMNI_REALTIME_URL"] ?? defaultURL
    var voice = ProcessInfo.processInfo.environment["VOICE_MODEL_VOICE"] ?? "Tina"
    var apiKey = ProcessInfo.processInfo.environment["DASHSCOPE_API_KEY"] ?? ""
    var instructions = "你是中文语音助手。请用简短口语回答用户。"
    var outputGain: Float = 0.8
    var threshold: Double = 0.65
    var silenceMs = 800
    var prefixMs = 300
    var serverVAD = false
    var verboseEvents = false
    var muteMicDuringPlayback = false
    var playbackHoldMs = 500
}

private final class EventID {
    static func make() -> String {
        "event_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
    }
}

private final class Args {
    static func parse() -> Config {
        var config = Config()
        var iterator = CommandLine.arguments.dropFirst().makeIterator()
        while let arg = iterator.next() {
            switch arg {
            case "--model":
                config.model = iterator.next() ?? config.model
            case "--url":
                config.url = iterator.next() ?? config.url
            case "--voice":
                config.voice = iterator.next() ?? config.voice
            case "--api-key":
                config.apiKey = iterator.next() ?? config.apiKey
            case "--instructions":
                config.instructions = iterator.next() ?? config.instructions
            case "--output-gain":
                config.outputGain = Float(iterator.next() ?? "") ?? config.outputGain
            case "--threshold":
                config.threshold = Double(iterator.next() ?? "") ?? config.threshold
            case "--silence-ms":
                config.silenceMs = Int(iterator.next() ?? "") ?? config.silenceMs
            case "--prefix-ms":
                config.prefixMs = Int(iterator.next() ?? "") ?? config.prefixMs
            case "--server-vad":
                config.serverVAD = true
            case "--verbose-events":
                config.verboseEvents = true
            case "--mute-mic-during-playback":
                config.muteMicDuringPlayback = true
            case "--keep-mic-during-playback":
                config.muteMicDuringPlayback = false
            case "--playback-hold-ms":
                config.playbackHoldMs = Int(iterator.next() ?? "") ?? config.playbackHoldMs
            case "-h", "--help":
                print(Self.helpText())
                exit(0)
            default:
                fputs("Unknown argument: \(arg)\n\n\(Self.helpText())\n", stderr)
                exit(2)
            }
        }
        return config
    }

    static func helpText() -> String {
        """
        Usage:
          swift openaiglass-sdk/server-python/devtools/omni_voiceprocessing_loopback.swift [options]

        Options:
          --api-key KEY          DashScope API key. Defaults to DASHSCOPE_API_KEY.
          --model NAME           Omni realtime model. Default: \(defaultModel)
          --url URL              Omni realtime base websocket URL.
          --voice NAME           Output voice. Default: Tina
          --output-gain VALUE    Local playback gain. Default: 0.8
          --server-vad           Use server_vad instead of semantic_vad.
          --threshold VALUE      VAD threshold. Default: 0.65
          --silence-ms VALUE     VAD silence duration. Default: 800
          --prefix-ms VALUE      VAD prefix padding. Default: 300
          --instructions TEXT    Realtime session instructions.
          --verbose-events       Print full server events.
          --mute-mic-during-playback
                                 Force half-duplex playback gate. Disabled by default because this tool validates full duplex.
          --keep-mic-during-playback
                                 Deprecated alias; full duplex is already the default.
          --playback-hold-ms N   Extra microphone mute time after each playback chunk. Default: 500
        """
    }
}

private final class OmniRealtimeWebSocket {
    private let config: Config
    private let task: URLSessionWebSocketTask
    private let playback: PcmPlayback
    private let encoder = JSONEncoder()
    private let sendQueue = DispatchQueue(label: "omni.websocket.send")
    private var closed = false

    init(config: Config, playback: PcmPlayback) {
        self.config = config
        self.playback = playback
        var components = URLComponents(string: config.url)!
        components.queryItems = [URLQueryItem(name: "model", value: config.model)]
        var request = URLRequest(url: components.url!)
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("OpenAIglassesDemo/voiceprocessing-loopback", forHTTPHeaderField: "User-Agent")
        self.task = URLSession(configuration: .default).webSocketTask(with: request)
    }

    func connect() {
        task.resume()
        receiveLoop()
        print("[local] websocket connecting...")
        sendSessionUpdate()
    }

    func close() {
        closed = true
        task.cancel(with: .goingAway, reason: nil)
    }

    func appendAudio(_ pcm16: Data) {
        guard !closed, !pcm16.isEmpty else { return }
        send([
            "event_id": EventID.make(),
            "type": "input_audio_buffer.append",
            "audio": pcm16.base64EncodedString(),
        ], log: false)
    }

    private func sendSessionUpdate() {
        let turnType = config.serverVAD ? "server_vad" : "semantic_vad"
        let session: [String: Any] = [
            "modalities": ["text", "audio"],
            "voice": config.voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": ["model": "paraformer-realtime-v2"],
            "turn_detection": [
                "type": turnType,
                "threshold": config.threshold,
                "prefix_padding_ms": config.prefixMs,
                "silence_duration_ms": config.silenceMs,
                "create_response": true,
                "interrupt_response": true,
            ],
            "instructions": config.instructions,
        ]
        send([
            "event_id": EventID.make(),
            "type": "session.update",
            "session": session,
        ])
    }

    private func send(_ object: [String: Any], log: Bool = true) {
        guard JSONSerialization.isValidJSONObject(object) else { return }
        guard let data = try? JSONSerialization.data(withJSONObject: object),
              let text = String(data: data, encoding: .utf8)
        else { return }
        if log {
            print("[client] \(text)")
        }
        sendQueue.async {
            self.task.send(.string(text)) { error in
                if let error {
                    fputs("[omni] send failed: \(error)\n", stderr)
                }
            }
        }
    }

    private func receiveLoop() {
        task.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                if !self.closed {
                    fputs("[omni] receive failed: \(error)\n", stderr)
                }
            case .success(let message):
                switch message {
                case .string(let text):
                    self.handle(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self.handle(text)
                    }
                @unknown default:
                    break
                }
                if !self.closed {
                    self.receiveLoop()
                }
            }
        }
    }

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            print("[omni] non-json: \(text.prefix(200))")
            return
        }
        let type = json["type"] as? String ?? ""
        if config.verboseEvents || shouldPrint(type) {
            print("[omni] \(summary(type: type, json: json))")
        }
        if type == "response.audio.delta",
           let delta = json["delta"] as? String,
           let audio = Data(base64Encoded: delta)
        {
            playback.enqueue(audio)
        }
    }

    private func shouldPrint(_ type: String) -> Bool {
        !["response.audio.delta"].contains(type)
    }

    private func summary(type: String, json: [String: Any]) -> String {
        if type == "response.audio_transcript.delta" {
            return "type=\(type) delta=\(json["delta"] ?? "")"
        }
        if type == "conversation.item.input_audio_transcription.completed" {
            return "type=\(type) transcript=\(json["transcript"] ?? "")"
        }
        if type == "response.audio_transcript.done" {
            return "type=\(type) transcript=\(json["transcript"] ?? "")"
        }
        return "type=\(type)"
    }
}

private final class PcmPlayback {
    private let engine: AVAudioEngine
    private let player = AVAudioPlayerNode()
    private let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: outputSampleRate, channels: channelCount, interleaved: false)!
    private let gain: Float
    private let stateQueue = DispatchQueue(label: "omni.playback.state")
    private let playbackHoldSeconds: Double
    private var activeUntil = Date.distantPast

    init(engine: AVAudioEngine, gain: Float, playbackHoldMs: Int) {
        self.engine = engine
        self.gain = max(0, min(gain, 1.5))
        self.playbackHoldSeconds = max(0.0, Double(playbackHoldMs) / 1000.0)
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func start() {
        player.play()
    }

    func enqueue(_ pcm16: Data) {
        let sampleCount = pcm16.count / 2
        guard sampleCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sampleCount))
        else { return }
        buffer.frameLength = AVAudioFrameCount(sampleCount)
        guard let channel = buffer.floatChannelData?[0] else { return }
        pcm16.withUnsafeBytes { raw in
            let ptr = raw.bindMemory(to: Int16.self)
            for index in 0..<sampleCount {
                channel[index] = Float(ptr[index]) / 32768.0 * gain
            }
        }
        let duration = Double(sampleCount) / outputSampleRate
        stateQueue.sync {
            let base = max(Date(), activeUntil)
            activeUntil = base.addingTimeInterval(duration + playbackHoldSeconds)
        }
        player.scheduleBuffer(buffer, completionHandler: nil)
    }

    func isPlaybackActive() -> Bool {
        stateQueue.sync {
            Date() < activeUntil
        }
    }
}

private final class InputResampler {
    private var sourceRemainder: [Float] = []

    func convert(buffer: AVAudioPCMBuffer) -> Data {
        guard let source = buffer.floatChannelData?[0] else { return Data() }
        let sourceRate = buffer.format.sampleRate
        let frames = Int(buffer.frameLength)
        guard frames > 0, sourceRate > 0 else { return Data() }
        var samples = sourceRemainder
        samples.reserveCapacity(sourceRemainder.count + frames)
        for index in 0..<frames {
            samples.append(source[index])
        }
        let ratio = sourceRate / inputSampleRate
        var position = 0.0
        var output = Data()
        while Int(position + 1) < samples.count {
            let leftIndex = Int(position)
            let fraction = Float(position - Double(leftIndex))
            let value = samples[leftIndex] * (1 - fraction) + samples[leftIndex + 1] * fraction
            let clipped = max(-1.0, min(0.999969, value))
            var intSample = Int16(clipped * 32767)
            withUnsafeBytes(of: &intSample) { output.append(contentsOf: $0) }
            position += ratio
        }
        let consumed = max(0, Int(position))
        sourceRemainder = consumed < samples.count ? Array(samples[consumed...]) : []
        return output
    }
}

private final class VoiceProcessingAudio {
    private let engine = AVAudioEngine()
    private let resampler = InputResampler()
    private let websocket: OmniRealtimeWebSocket
    private let playback: PcmPlayback
    private let config: Config

    init(config: Config) {
        self.config = config
        self.playback = PcmPlayback(engine: engine, gain: config.outputGain, playbackHoldMs: config.playbackHoldMs)
        self.websocket = OmniRealtimeWebSocket(config: config, playback: playback)
    }

    func start() throws {
        try configureVoiceProcessing()
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            guard let self else { return }
            if self.config.muteMicDuringPlayback && self.playback.isPlaybackActive() {
                return
            }
            let pcm16 = self.resampler.convert(buffer: buffer)
            self.websocket.appendAudio(pcm16)
        }
        engine.prepare()
        websocket.connect()
        try engine.start()
        playback.start()
        print("[local] running with macOS VoiceProcessingIO. Press Ctrl+C to stop.")
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        websocket.close()
    }

    private func configureVoiceProcessing() throws {
        try engine.inputNode.setVoiceProcessingEnabled(true)
        try engine.outputNode.setVoiceProcessingEnabled(true)
        engine.inputNode.isVoiceProcessingInputMuted = false
        print("[local] voice processing enabled")
    }
}

private let config = Args.parse()
if config.apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
    fputs("Missing DASHSCOPE_API_KEY. Set it in env or pass --api-key.\n", stderr)
    exit(2)
}

private let audio = VoiceProcessingAudio(config: config)
signal(SIGINT) { _ in
    print("\n[local] stopping...")
    audio.stop()
    exit(0)
}
signal(SIGTERM) { _ in
    audio.stop()
    exit(0)
}
try audio.start()
RunLoop.main.run()
