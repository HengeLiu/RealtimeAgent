package com.audiochat.phone.audio

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 音频采集器
 * 完全复刻 Python 端的音频采集功能：
 * - 16kHz 采样率
 * - 16-bit PCM
 * - 单声道
 * - 20ms 一帧 (320 samples)
 */
class AudioCaptureManager(
    private val context: Context,
    private val onAudioData: ((pcmData: ByteArray) -> Unit)? = null
) {
    companion object {
        private const val TAG = "AudioCapture"

        val SAMPLE_RATE = AudioConstants.INPUT_SAMPLE_RATE
        val CHUNK_SIZE = AudioConstants.INPUT_CHUNK_SIZE
        private val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        private val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
    }

    private var audioRecord: AudioRecord? = null
    private var captureJob: Job? = null
    private var isCapturing = false

    /**
     * 初始化音频采集
     */
    fun initialize(): Boolean {
        return try {
            val minBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)

            if (minBufferSize == AudioRecord.ERROR || minBufferSize == AudioRecord.ERROR_BAD_VALUE) {
                Log.e(TAG, "不支持的音频配置")
                return false
            }

            val bufferSize = maxOf(minBufferSize, CHUNK_SIZE)

            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                CHANNEL_CONFIG,
                AUDIO_FORMAT,
                bufferSize
            )

            if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord 初始化失败")
                return false
            }

            Log.i(TAG, "AudioRecord 初始化成功: buffer=$bufferSize bytes")
            true
        } catch (e: Exception) {
            Log.e(TAG, "初始化音频采集失败", e)
            false
        }
    }

    /**
     * 开始音频采集
     */
    fun startCapture() {
        if (isCapturing || audioRecord == null) return

        try {
            audioRecord?.startRecording()
            isCapturing = true

            captureJob = CoroutineScope(Dispatchers.IO).launch {
                val buffer = ByteArray(CHUNK_SIZE)

                while (isActive && isCapturing) {
                    try {
                        val read = audioRecord?.read(buffer, 0, buffer.size) ?: -1

                        if (read > 0) {
                            // 回调音频数据
                            onAudioData?.invoke(if (read == buffer.size) buffer else buffer.copyOf(read))
                        } else if (read < 0) {
                            Log.w(TAG, "读取音频数据错误: $read")
                        }
                    } catch (e: Exception) {
                        if (isActive) {
                            Log.e(TAG, "读取音频异常", e)
                        }
                    }
                }
            }

            Log.i(TAG, "音频采集已开始: ${SAMPLE_RATE}Hz, 16bit, mono")
        } catch (e: Exception) {
            Log.e(TAG, "开始音频采集失败", e)
            isCapturing = false
        }
    }

    /**
     * 停止音频采集
     */
    fun stopCapture() {
        isCapturing = false

        try {
            captureJob?.cancel()
            captureJob = null

            audioRecord?.stop()
            Log.i(TAG, "音频采集已停止")
        } catch (e: Exception) {
            Log.e(TAG, "停止音频采集失败", e)
        }
    }

    /**
     * 释放资源
     */
    fun release() {
        stopCapture()

        try {
            audioRecord?.release()
            audioRecord = null
        } catch (e: Exception) {
            Log.e(TAG, "释放资源失败", e)
        }
    }

    val capturing: Boolean
        get() = isCapturing
}