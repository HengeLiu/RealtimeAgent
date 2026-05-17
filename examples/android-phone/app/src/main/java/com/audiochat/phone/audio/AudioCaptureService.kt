package com.audiochat.phone.audio

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Binder
import android.os.IBinder
import android.os.PowerManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.app.NotificationCompat
import com.audiochat.phone.device.DeviceManager
import com.audiochat.phone.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * 前台服务 - 保持设备常连接
 * 用于在后台保持 WebSocket 连接
 */
class AudioCaptureService : Service() {
    companion object {
        private const val TAG = "AudioCaptureService"
        private const val CHANNEL_ID = "audio_chat_foreground"
        private const val NOTIFICATION_ID = 1

        const val ACTION_START = "com.audiochat.phone.action.START_SERVICE"
        const val ACTION_STOP = "com.audiochat.phone.action.STOP_SERVICE"
        const val EXTRA_SERVER_URL = "server_url"
        const val EXTRA_USER_ID = "user_id"
        const val EXTRA_DEVICE_ID = "device_id"
        const val EXTRA_ACCESS_TOKEN = "access_token"
        const val EXTRA_DEVICE_NAME = "device_name"
    }

    private var deviceManager: DeviceManager? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private val binder = LocalBinder()
    private var serviceJob: Job? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    inner class LocalBinder : Binder() {
        fun getService(): AudioCaptureService = this@AudioCaptureService
    }

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "服务创建")
        createNotificationChannel()
        acquireWakeLock()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.i(TAG, "服务启动: action=${intent?.action}")

        when (intent?.action) {
            ACTION_START -> {
                val serverUrl = intent.getStringExtra(EXTRA_SERVER_URL) ?: return START_NOT_STICKY
                val userId = intent.getStringExtra(EXTRA_USER_ID) ?: return START_NOT_STICKY
                val deviceId = intent.getStringExtra(EXTRA_DEVICE_ID) ?: return START_NOT_STICKY
                val accessToken = intent.getStringExtra(EXTRA_ACCESS_TOKEN)
                val deviceName = intent.getStringExtra(EXTRA_DEVICE_NAME) ?: "android-phone"

                startForeground(NOTIFICATION_ID, createNotification("正在连接..."))
                startConnection(serverUrl, userId, deviceId, deviceName, accessToken)
            }
            ACTION_STOP -> {
                stopConnection()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
            else -> {
                // Service 重启时恢复连接
                val serverUrl = intent?.getStringExtra(EXTRA_SERVER_URL)
                val userId = intent?.getStringExtra(EXTRA_USER_ID)
                val deviceId = intent?.getStringExtra(EXTRA_DEVICE_ID)
                val accessToken = intent?.getStringExtra(EXTRA_ACCESS_TOKEN)
                val deviceName = intent?.getStringExtra(EXTRA_DEVICE_NAME) ?: "android-phone"

                if (serverUrl != null && userId != null && deviceId != null) {
                    startForeground(NOTIFICATION_ID, createNotification("正在重连..."))
                    startConnection(serverUrl, userId, deviceId, deviceName, accessToken)
                }
            }
        }

        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder {
        return binder
    }

    override fun onDestroy() {
        Log.i(TAG, "服务销毁")
        releaseWakeLock()
        serviceJob?.cancel()
        deviceManager?.disconnect()
        deviceManager = null
        super.onDestroy()
    }

    private fun startConnection(serverUrl: String, userId: String, deviceId: String, deviceName: String, accessToken: String?) {
        serviceJob?.cancel()

        deviceManager = DeviceManager(
            serverUrl = serverUrl,
            userId = userId,
            deviceId = deviceId,
            accessToken = accessToken,
            deviceName = deviceName
        )

        serviceJob = CoroutineScope(Dispatchers.IO).launch {
            try {
                deviceManager?.connectAndRegister()
                deviceManager?.startStreamAndHeartbeat()

                mainHandler.post {
                    updateNotification("已连接 - 常驻运行中")
                }

                deviceManager?.onDeviceRegistered = {
                    mainHandler.post {
                        updateNotification("已注册 - 常驻运行中")
                    }
                }

                deviceManager?.onReconnectNeeded = {
                    mainHandler.post {
                        updateNotification("连接断开，正在重连...")
                    }
                }

            } catch (e: Exception) {
                Log.e(TAG, "连接失败", e)
                mainHandler.post {
                    updateNotification("连接失败，点击重试")
                }
            }
        }
    }

    private fun stopConnection() {
        serviceJob?.cancel()
        serviceJob = null
        deviceManager?.disconnect()
        deviceManager = null
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "音频聊天服务",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "保持设备与服务器的连接"
            setShowBadge(false)
        }

        val notificationManager = getSystemService(NotificationManager::class.java)
        notificationManager.createNotificationChannel(channel)
    }

    private fun createNotification(status: String): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, AudioCaptureService::class.java).apply { action = ACTION_STOP },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("AudioChat 手机端")
            .setContentText(status)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(pendingIntent)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "停止", stopIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    private fun updateNotification(status: String) {
        val notification = createNotification(status)
        val notificationManager = getSystemService(NotificationManager::class.java)
        notificationManager.notify(NOTIFICATION_ID, notification)
    }

    private fun acquireWakeLock() {
        val powerManager = getSystemService(POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "AudioChat::AudioCaptureServiceWakeLock"
        ).apply {
            acquire(10 * 60 * 60 * 1000L) // 10小时超时
        }
        Log.i(TAG, "WakeLock 已获取")
    }

    private fun releaseWakeLock() {
        wakeLock?.let {
            if (it.isHeld) {
                it.release()
                Log.i(TAG, "WakeLock 已释放")
            }
        }
        wakeLock = null
    }
}