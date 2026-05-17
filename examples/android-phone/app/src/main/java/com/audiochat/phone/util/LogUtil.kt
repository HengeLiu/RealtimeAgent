package com.audiochat.phone.util

import android.util.Log

/**
 * Log 工具类
 * 统一管理日志输出
 */
object LogUtil {
    private const val DEFAULT_TAG = "AudioChat"

    fun d(message: String, tag: String = DEFAULT_TAG) = Log.d(tag, message)
    fun i(message: String, tag: String = DEFAULT_TAG) = Log.i(tag, message)
    fun w(message: String, tag: String = DEFAULT_TAG) = Log.w(tag, message)
    fun e(message: String, throwable: Throwable? = null, tag: String = DEFAULT_TAG) {
        if (throwable != null) {
            Log.e(tag, message, throwable)
        } else {
            Log.e(tag, message)
        }
    }

    fun timestamp(): String {
        val sdf = java.text.SimpleDateFormat("HH:mm:ss.SSS", java.util.Locale.getDefault())
        return sdf.format(java.util.Date())
    }

    fun logD(tag: String, message: String) = d("[${timestamp()}] $message", tag)
    fun logI(tag: String, message: String) = i("[${timestamp()}] $message", tag)
    fun logW(tag: String, message: String) = w("[${timestamp()}] $message", tag)
    fun logE(tag: String, message: String, e: Throwable? = null) = e("[${timestamp()}] $message", e, tag)
}

/**
 * 设备相关的常量
 */
object DeviceUtil {
    const val TAG_DEVICE = "DeviceManager"
    const val TAG_WEBSOCKET = "WebSocket"
    const val TAG_SERVICE = "AudioCaptureService"
}