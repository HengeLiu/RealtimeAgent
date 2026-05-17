package com.audiochat.phone.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 音频播放器
 * 完全复刻 Python 端的音频播放功能：
 * - 24kHz 采样率（Server 配置）
 * - 16-bit PCM
 * - 单声道
 */
class AudioPlaybackManager(
    private val context: Context
) {
    companion object {
        private const val TAG = "AudioPlayback"

        val SAMPLE_RATE = AudioConstants.OUTPUT_SAMPLE_RATE
        val CHUNK_SIZE = AudioConstants.OUTPUT_CHUNK_SIZE
        private val CHANNEL_CONFIG = AudioFormat.CHANNEL_OUT_MONO
        private val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT
    }

    private var audioTrack: AudioTrack? = null
    private var playbackThread: Thread? = null
    private val isPlaying = AtomicBoolean(false)
    private val audioQueue = LinkedBlockingQueue<ByteArray>(100)

    /**
     * 初始化音频播放
     */
    fun initialize(): Boolean {
        return try {
            val minBufferSize = AudioTrack.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)

            if (minBufferSize == AudioTrack.ERROR || minBufferSize == AudioTrack.ERROR_BAD_VALUE) {
                Log.e(TAG, "不支持的音频播放配置")
                return false
            }

            val bufferSize = maxOf(minBufferSize, CHUNK_SIZE)

            audioTrack = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANT)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build()
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setSampleRate(SAMPLE_RATE)
                        .setChannelMask(CHANNEL_CONFIG)
                        .setEncoding(AUDIO_FORMAT)
                        .build()
                )
                .setBufferSizeInBytes(bufferSize)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()

            Log.i(TAG, "AudioTrack 初始化成功: buffer=$bufferSize bytes")
            true
        } catch (e: Exception) {
            Log.e(TAG, "初始化音频播放失败", e)
            false
        }
    }

    /**
     * 开始播放
     */
    fun startPlayback() {
        if (isPlaying.get() || audioTrack == null) return

        audioTrack?.play()
        isPlaying.set(true)

        playbackThread = Thread({
            while (isPlaying.get()) {
                try {
                    val data = audioQueue.take() ?: continue

                    if (!isPlaying.get()) break

                    val written = audioTrack?.write(data, 0, data.size) ?: -1

                    if (written < 0) {
                        Log.w(TAG, "写入音频数据错误: $written")
                    }
                } catch (e: InterruptedException) {
                    Thread.currentThread().interrupt()
                    break
                } catch (e: Exception) {
                    Log.e(TAG, "播放异常", e)
                }
            }
        }, "audio-playback-thread").apply {
            start()
        }

        Log.i(TAG, "音频播放已开始: ${SAMPLE_RATE}Hz, 16bit, mono")
    }

    /**
     * 写入音频数据
     */
    fun write(pcmData: ByteArray): Boolean {
        if (!isPlaying.get()) return false

        return audioQueue.offer(pcmData)
    }

    /**
     * 停止播放
     */
    fun stopPlayback() {
        isPlaying.set(false)

        try {
            playbackThread?.interrupt()
            playbackThread = null

            audioTrack?.stop()
            audioTrack?.flush()

            audioQueue.clear()

            Log.i(TAG, "音频播放已停止")
        } catch (e: Exception) {
            Log.e(TAG, "停止播放失败", e)
        }
    }

    /**
     * 释放资源
     */
    fun release() {
        stopPlayback()

        try {
            audioTrack?.release()
            audioTrack = null
        } catch (e: Exception) {
            Log.e(TAG, "释放资源失败", e)
        }
    }

    val playing: Boolean
        get() = isPlaying.get()

    val queueSize: Int
        get() = audioQueue.size
}