import AVFoundation
import Foundation

enum ConvertError: Error {
    case invalidArguments
    case cannotCreateConverter
    case conversionFailed(String)
}

func appendLittleEndian<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
    var little = value.littleEndian
    withUnsafeBytes(of: &little) { rawBuffer in
        data.append(contentsOf: rawBuffer)
    }
}

func buildWavData(pcmData: Data, sampleRate: UInt32, channels: UInt16, bitDepth: UInt16) -> Data {
    let blockAlign = UInt16(channels * bitDepth / 8)
    let byteRate = UInt32(sampleRate) * UInt32(blockAlign)
    let riffSize = UInt32(36 + pcmData.count)

    var data = Data()
    data.append("RIFF".data(using: .ascii)!)
    appendLittleEndian(riffSize, to: &data)
    data.append("WAVE".data(using: .ascii)!)
    data.append("fmt ".data(using: .ascii)!)
    appendLittleEndian(UInt32(16), to: &data)
    appendLittleEndian(UInt16(1), to: &data)
    appendLittleEndian(channels, to: &data)
    appendLittleEndian(sampleRate, to: &data)
    appendLittleEndian(byteRate, to: &data)
    appendLittleEndian(blockAlign, to: &data)
    appendLittleEndian(bitDepth, to: &data)
    data.append("data".data(using: .ascii)!)
    appendLittleEndian(UInt32(pcmData.count), to: &data)
    data.append(pcmData)
    return data
}

func main() throws {
    guard CommandLine.arguments.count == 3 else {
        throw ConvertError.invalidArguments
    }

    let inputURL = URL(fileURLWithPath: CommandLine.arguments[1])
    let outputURL = URL(fileURLWithPath: CommandLine.arguments[2])

    let inputFile = try AVAudioFile(forReading: inputURL)
    guard let outputFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32,
        sampleRate: 16000,
        channels: 1,
        interleaved: false
    ) else {
        throw ConvertError.conversionFailed("无法创建目标音频格式")
    }
    guard let converter = AVAudioConverter(from: inputFile.processingFormat, to: outputFormat) else {
        throw ConvertError.cannotCreateConverter
    }

    if FileManager.default.fileExists(atPath: outputURL.path) {
        try FileManager.default.removeItem(at: outputURL)
    }

    let inputCapacity = AVAudioFrameCount(max(inputFile.length, 1))
    guard let inputBuffer = AVAudioPCMBuffer(
        pcmFormat: inputFile.processingFormat,
        frameCapacity: inputCapacity
    ) else {
        throw ConvertError.conversionFailed("无法创建输入缓冲区")
    }
    try inputFile.read(into: inputBuffer)
    if inputBuffer.frameLength == 0 {
        throw ConvertError.conversionFailed("输入音频为空")
    }

    let ratio = outputFormat.sampleRate / inputFile.processingFormat.sampleRate
    let outputCapacity = AVAudioFrameCount(Double(inputBuffer.frameLength) * ratio) + 1024
    guard let outputBuffer = AVAudioPCMBuffer(
        pcmFormat: outputFormat,
        frameCapacity: outputCapacity
    ) else {
        throw ConvertError.conversionFailed("无法创建输出缓冲区")
    }

    var suppliedInput = false
    var conversionError: NSError?
    let status = converter.convert(to: outputBuffer, error: &conversionError) { _, outStatus in
        if suppliedInput {
            outStatus.pointee = .endOfStream
            return nil
        }
        suppliedInput = true
        outStatus.pointee = .haveData
        return inputBuffer
    }

    if let conversionError {
        throw ConvertError.conversionFailed(conversionError.localizedDescription)
    }
    switch status {
    case .haveData, .inputRanDry, .endOfStream:
        break
    case .error:
        throw ConvertError.conversionFailed("转换状态返回 error")
    @unknown default:
        throw ConvertError.conversionFailed("转换状态未知")
    }

    guard outputBuffer.frameLength > 0, let channelData = outputBuffer.floatChannelData else {
        throw ConvertError.conversionFailed("转换后未生成可用音频样本")
    }

    var outputPCM = Data(capacity: Int(outputBuffer.frameLength) * MemoryLayout<Int16>.size)
    for index in 0..<Int(outputBuffer.frameLength) {
        let sample = max(-1.0, min(1.0, channelData[0][index]))
        let scaled = Int16(sample * Float(Int16.max))
        appendLittleEndian(scaled, to: &outputPCM)
    }

    let wavData = buildWavData(
        pcmData: outputPCM,
        sampleRate: 16000,
        channels: 1,
        bitDepth: 16
    )
    try wavData.write(to: outputURL)
}

do {
    try main()
} catch {
    fputs("convert_audio_to_phase_c failed: \(error)\n", stderr)
    exit(1)
}

