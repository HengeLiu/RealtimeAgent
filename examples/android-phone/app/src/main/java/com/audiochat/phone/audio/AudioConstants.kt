package com.audiochat.phone.audio

/**
 * 音频常量定义
 * 统一管理输入输出的采样率、格式等配置
 */
object AudioConstants {
    // ===== 输入 (采集) 配置 =====
    const val INPUT_SAMPLE_RATE = 16000
    const val INPUT_CHANNELS = 1
    const val INPUT_CHUNK_MS = 20  // 20ms 一帧
    val INPUT_CHUNK_SIZE: Int get() = INPUT_SAMPLE_RATE * INPUT_CHUNK_MS / 1000 * INPUT_CHANNELS * 2 // 640 bytes

    // ===== 输出 (播放) 配置 =====
    const val OUTPUT_SAMPLE_RATE = 24000
    const val OUTPUT_CHANNELS = 1
    const val OUTPUT_CHUNK_MS = 100  // 100ms buffer
    val OUTPUT_CHUNK_SIZE: Int get() = OUTPUT_SAMPLE_RATE * OUTPUT_CHUNK_MS / 1000 * OUTPUT_CHANNELS * 2 // 4800 bytes
}