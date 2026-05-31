import Foundation

/// PCM 音频转换工具。
///
/// 主要功能：把 App 采集或合成的浮点采样转换为 realtime-agent 当前约定的 PCM16LE bytes。
/// 说明：真实重采样仍建议由 App 使用 AVAudioConverter 完成，本工具只负责样本格式转换。
public enum AudioPCMConverter {
    /// 将 Float32 采样转换为 PCM16LE。
    ///
    /// 参数：`samples` 为范围通常在 -1.0...1.0 的单声道浮点采样。
    /// 返回值：小端序 Int16 PCM 数据，每个采样 2 字节。
    /// 异常情况：本函数不抛错；超出范围的值会被夹到 -1.0...1.0。
    public static func pcm16LE(fromFloat32 samples: [Float]) -> Data {
        var data = Data()
        data.reserveCapacity(samples.count * 2)
        for sample in samples {
            let clipped = min(1.0, max(-1.0, sample))
            let scaled = clipped < 0 ? clipped * 32768.0 : clipped * 32767.0
            let value = Int16(scaled.rounded())
            var littleEndian = value.littleEndian
            withUnsafeBytes(of: &littleEndian) { data.append(contentsOf: $0) }
        }
        return data
    }

    /// 计算 PCM16LE 期望字节数。
    public static func expectedPCM16ByteCount(sampleRate: Int, channels: Int, durationMS: Int) -> Int {
        max(0, sampleRate) * max(0, channels) * max(0, durationMS) / 1000 * 2
    }
}
