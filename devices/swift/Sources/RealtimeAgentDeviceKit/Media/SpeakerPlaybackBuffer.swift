import Foundation

/// SDK 内置 speaker 播放 buffer。
///
/// 主要功能：按音频时长维护下行缓冲，并按 chunk seq 连续写出到播放器。
public actor SpeakerPlaybackBuffer {
    public private(set) var bufferedMS = 0
    public private(set) var bufferedBytes = 0
    public private(set) var isPaused = false
    public private(set) var hasStarted = false
    public private(set) var outOfOrderChunks = 0
    public private(set) var duplicateChunks = 0

    private let configuration: PlaybackBuffer
    private let sink: RealtimeAgentSpeakerSink
    private var pendingChunks: [Int: RealtimeAgentStreamChunk] = [:]
    private var nextDrainSeq: Int?
    private var previousAppendSeq: Int?
    private var hasDrainedAnyChunk = false

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
            nextDrainSeq: nextDrainSeq,
            pendingMinSeq: pendingChunks.keys.min(),
            pendingMaxSeq: pendingChunks.keys.max(),
            outOfOrderChunks: outOfOrderChunks,
            duplicateChunks: duplicateChunks,
            isPaused: isPaused,
            hasStarted: hasStarted
        )
    }

    /// 追加一帧 speaker chunk。
    ///
    /// 主要逻辑：control 和 stream 任务可能并发处理同一条输出流，actor 只能保证状态串行，
    /// 不能保证不同任务进入 actor 的顺序等于网络 chunk seq 顺序，因此这里先按 seq 暂存。
    ///
    /// 返回值：本次追加触发的协议动作，调用方据此发送 started / pause / resume。
    public func append(_ chunk: RealtimeAgentStreamChunk) async throws -> [SpeakerPlaybackAction] {
        if let previousAppendSeq, chunk.seq < previousAppendSeq {
            outOfOrderChunks += 1
        }
        previousAppendSeq = chunk.seq

        if let nextDrainSeq, chunk.seq < nextDrainSeq {
            duplicateChunks += 1
            return []
        }
        if pendingChunks[chunk.seq] != nil {
            duplicateChunks += 1
            return []
        }

        pendingChunks[chunk.seq] = chunk
        if let nextDrainSeq {
            if !hasDrainedAnyChunk, chunk.seq < nextDrainSeq {
                self.nextDrainSeq = chunk.seq
            }
        } else {
            nextDrainSeq = chunk.seq
        }

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
    /// 说明：由 SDK drain loop 调用。只有下一帧 seq 已经到齐时才写出，避免句首乱序 chunk
    /// 被直接送进播放器造成短暂破音或卡顿。
    public func drainNext() async throws -> [SpeakerPlaybackAction] {
        guard let seq = nextDrainSeq ?? pendingChunks.keys.min(),
              let chunk = pendingChunks.removeValue(forKey: seq) else {
            return []
        }
        nextDrainSeq = seq + 1
        hasDrainedAnyChunk = true

        try await sink.write(chunk)

        bufferedMS = max(0, bufferedMS - max(chunk.durationMS, 0))
        bufferedBytes = max(0, bufferedBytes - chunk.payload.count)
        if isPaused && bufferedMS <= configuration.lowWatermarkMS {
            isPaused = false
            return [.resume(bufferedMS: bufferedMS, lowWatermarkMS: configuration.lowWatermarkMS)]
        }
        return []
    }

    /// 写出当前队列到 speaker sink。
    public func drainAvailable() async throws -> [SpeakerPlaybackAction] {
        var actions: [SpeakerPlaybackAction] = []
        while hasDrainableChunk {
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
        !hasDrainableChunk
    }

    /// 取消播放并清空 buffer。
    public func cancel() async {
        pendingChunks.removeAll()
        nextDrainSeq = nil
        previousAppendSeq = nil
        hasDrainedAnyChunk = false
        bufferedMS = 0
        bufferedBytes = 0
        isPaused = false
        hasStarted = false
        outOfOrderChunks = 0
        duplicateChunks = 0
        await sink.cancel()
    }

    private var queuedChunkCount: Int {
        pendingChunks.count
    }

    private var hasDrainableChunk: Bool {
        guard let seq = nextDrainSeq ?? pendingChunks.keys.min() else {
            return false
        }
        return pendingChunks[seq] != nil
    }
}

/// speaker 播放缓冲的只读诊断快照。
public struct SpeakerPlaybackBufferSnapshot: Equatable, Sendable {
    public let bufferedMS: Int
    public let bufferedBytes: Int
    public let queuedChunks: Int
    public let nextDrainSeq: Int?
    public let pendingMinSeq: Int?
    public let pendingMaxSeq: Int?
    public let outOfOrderChunks: Int
    public let duplicateChunks: Int
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
