import Foundation

/// 简单扬声器输出缓冲器。
///
/// 主要功能：保存收到的 `actuator.speaker` chunk，供 App 的真实播放器按顺序消费。
public final class SpeakerPlayer: @unchecked Sendable {
    public private(set) var bufferedBytes: Int = 0
    public private(set) var chunks: [RealtimeAgentStreamChunk] = []

    /// 创建空的输出缓冲器。
    public init() {}

    /// 追加一帧输出 chunk。
    public func append(_ chunk: RealtimeAgentStreamChunk) {
        chunks.append(chunk)
        bufferedBytes += chunk.payload.count
    }

    /// 清空缓冲区。
    public func drain() {
        chunks.removeAll()
        bufferedBytes = 0
    }

    /// 将输出类型绑定到客户端。
    ///
    /// 说明：SDK 会把对应 stream type 的 chunk 追加到本缓冲器；真实播放可由 App 读取 `chunks` 后接管。
    public func bind(to client: RealtimeAgentDeviceClient, streamType: String = "actuator.speaker") {
        client.onOutputChunk(streamType) { [weak self] chunk, _ in
            self?.append(chunk)
        }
    }
}
