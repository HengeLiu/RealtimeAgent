import Foundation

/// SDK 内置 speaker 播放 buffer。
///
/// 主要功能：按音频时长维护下行缓冲，触发播放启动、高水位暂停和低水位恢复。
public actor SpeakerPlaybackBuffer {
    public private(set) var bufferedMS = 0
    public private(set) var bufferedBytes = 0
    public private(set) var isPaused = false
    public private(set) var hasStarted = false

    private let configuration: PlaybackBuffer
    private let sink: RealtimeAgentSpeakerSink
    private var queue: [RealtimeAgentStreamChunk] = []
    private var queueStartIndex = 0

    public init(configuration: PlaybackBuffer = .default, sink: RealtimeAgentSpeakerSink = RealtimeAgentNoopSpeakerSink()) {
        self.configuration = configuration
        self.sink = sink
    }

    /// 返回当前播放缓冲快照。
    ///
    /// 主要用途：端侧排查下行音频时，调用方可以把缓冲水位、队列长度和起播状态写入日志。
    public func snapshot() -> SpeakerPlaybackBufferSnapshot {
        SpeakerPlaybackBufferSnapshot(
            bufferedMS: bufferedMS,
            bufferedBytes: bufferedBytes,
            queuedChunks: queuedChunkCount,
            isPaused: isPaused,
            hasStarted: hasStarted
        )
    }

    /// 追加一帧 speaker chunk。
    ///
    /// 返回值：本次追加触发的协议动作，调用方据此发送 started / pause / resume。
    public func append(_ chunk: RealtimeAgentStreamChunk) async throws -> [SpeakerPlaybackAction] {
        queue.append(chunk)
        bufferedMS += max(chunk.durationMS, 0)
        bufferedBytes += chunk.payload.count
        var actions: [SpeakerPlaybackAction] = []
        if !hasStarted && bufferedMS >= configuration.startWatermarkMS {
            hasStarted = true
            actions.append(.started(bufferedMS: bufferedMS))
        }
        if !isPaused && bufferedMS >= configuration.highWatermarkMS {
            isPaused = true
            actions.append(.pause(bufferedMS: bufferedMS, highWatermarkMS: configuration.highWatermarkMS))
        }
        if bufferedMS > configuration.maxBufferMS {
            let overflow = bufferedMS - configuration.maxBufferMS
            actions.append(.overflow(bufferedMS: bufferedMS, overflowMS: overflow))
        }
        return actions
    }

    /// 写出下一帧到 speaker sink。
    ///
    /// 说明：由 SDK drain loop 调用，用来模拟“播放侧持续消费 SDK buffer”的过程。
    public func drainNext() async throws -> [SpeakerPlaybackAction] {
        guard queueStartIndex < queue.count else {
            return []
        }
        let chunk = queue[queueStartIndex]
        queueStartIndex += 1

        try await sink.write(chunk)

        bufferedMS = max(0, bufferedMS - max(chunk.durationMS, 0))
        bufferedBytes = max(0, bufferedBytes - chunk.payload.count)
        compactQueueIfNeeded()
        if isPaused && bufferedMS <= configuration.lowWatermarkMS {
            isPaused = false
            return [.resume(bufferedMS: bufferedMS, lowWatermarkMS: configuration.lowWatermarkMS)]
        }
        return []
    }

    /// 写出当前队列到 speaker sink。
    public func drainAvailable() async throws -> [SpeakerPlaybackAction] {
        var actions: [SpeakerPlaybackAction] = []
        while queueStartIndex < queue.count {
            actions.append(contentsOf: try await drainNext())
        }
        return actions
    }

    /// 等待本地播放器 drain。
    public func drainSink() async throws {
        _ = try await drainAvailable()
        try await sink.drain()
    }

    public var isEmpty: Bool {
        queueStartIndex >= queue.count
    }

    /// 取消播放并清空 buffer。
    public func cancel() async {
        queue.removeAll()
        queueStartIndex = 0
        bufferedMS = 0
        bufferedBytes = 0
        isPaused = false
        hasStarted = false
        await sink.cancel()
    }

    private var queuedChunkCount: Int {
        max(0, queue.count - queueStartIndex)
    }

    private func compactQueueIfNeeded() {
        guard queueStartIndex > 64, queueStartIndex * 2 >= queue.count else {
            return
        }
        queue.removeFirst(queueStartIndex)
        queueStartIndex = 0
    }
}

/// speaker 播放缓冲的只读诊断快照。
public struct SpeakerPlaybackBufferSnapshot: Equatable, Sendable {
    public let bufferedMS: Int
    public let bufferedBytes: Int
    public let queuedChunks: Int
    public let isPaused: Bool
    public let hasStarted: Bool
}

/// speaker buffer 触发的协议动作。
public enum SpeakerPlaybackAction: Equatable, Sendable {
    case started(bufferedMS: Int)
    case pause(bufferedMS: Int, highWatermarkMS: Int)
    case resume(bufferedMS: Int, lowWatermarkMS: Int)
    case overflow(bufferedMS: Int, overflowMS: Int)
}
