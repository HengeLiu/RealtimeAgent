package com.audiochat.phone.ui

import android.app.Application
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.provider.OpenableColumns
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.audiochat.phone.auth.AliyunAuthManager
import com.audiochat.phone.auth.TokenManager
import com.audiochat.phone.audio.AudioCaptureManager
import com.audiochat.phone.audio.AudioPlaybackManager
import com.audiochat.phone.audio.AudioCaptureService
import com.audiochat.phone.device.Device
import com.audiochat.phone.device.DeviceConfig
import com.audiochat.phone.device.DeviceListener
import com.audiochat.phone.device.DeviceManager
import com.audiochat.phone.device.DeviceIdManager
import com.audiochat.phone.error.ErrorHandler
import com.audiochat.phone.protocol.AudioChatEvent
import com.audiochat.phone.protocol.StreamChunk
import com.audiochat.phone.video.CameraManager
import com.audiochat.phone.video.PeerVideoTaskManager
import com.audiochat.phone.vision.YoloDetector
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response

data class EventLogEntry(
    val timestamp: String,
    val eventName: String,
    val direction: String,
    val detail: String
)

data class LogEntry(
    val timestamp: String,
    val level: String,
    val message: String
)

data class PhoneUiState(
    val serverUrl: String = "http://192.168.31.8:8765",
    val userId: String = "",
    val deviceId: String = "",

    val isConnected: Boolean = false,
    val isRegistered: Boolean = false,
    val isControlConnected: Boolean = false,
    val isStreamConnected: Boolean = false,
    val controlState: String = "idle",
    val streamState: String = "idle",

    val isPeerVideoConnected: Boolean = false,
    val peerVideoClientIp: String = "",
    val showPeerVideoToast: Boolean = false,
    val peerVideoToastMessage: String = "",

    val isCapturingAudio: Boolean = false,
    val isCameraActive: Boolean = false,
    val isSpeakerActive: Boolean = false,
    val isVibrating: Boolean = false,

    val audioSampleRate: Int = AudioCaptureManager.SAMPLE_RATE,
    val audioChunkSize: Int = 640,
    val audioFramesSent: Int = 0,
    val audioBytesSent: Long = 0L,
    val audioChunksPlayed: Int = 0,
    val playbackQueueSize: Int = 0,

    val heartbeatInterval: Long = 10_000L,
    val lastHeartbeatTime: Long = 0L,

    val eventsReceived: Int = 0,
    val controlEventsCount: Int = 0,
    val streamEventsCount: Int = 0,
    val commandEventsCount: Int = 0,
    val chunksSent: Int = 0,
    val imagesUploaded: Int = 0,
    val captureSuccessCount: Int = 0,
    val captureFailCount: Int = 0,
    val avgImageSize: String = "-",
    val jpegQuality: Int = 90,

    val sessionId: String = "",
    val lastCaptureResult: String = "",
    val lastImageProcessResult: String = "",
    val selectedImageUri: Uri? = null,
    val annotatedImageUri: Uri? = null,
    val captureHistory: List<String> = emptyList(),

    val eventFilter: String = "all",
    val filteredEvents: List<EventLogEntry> = emptyList(),
    val allEvents: List<EventLogEntry> = emptyList(),

    val logLevelFilter: String = "ALL",
    val filteredLogEntries: List<LogEntry> = emptyList(),
    val logEntries: List<LogEntry> = emptyList(),

    val rawMessages: List<String> = emptyList(),

    val currentFrame: PeerVideoTaskManager.FrameResult? = null,
    val peerVideoTaskState: PeerVideoTaskManager.TaskState? = null,
    val currentDetections: List<YoloDetector.Detection> = emptyList(),
    val framesProcessed: Int = 0,
    val objectsFound: Int = 0,

    // YOLO 状态
    val yoloModelName: String = "yolov8n",
    val yoloModelLoaded: Boolean = false,
    val yoloInferenceTimeMs: Long = 0L,
    val yoloLastDetectionCount: Int = 0,

    val isLoggedIn: Boolean = false,
    val userPhone: String = "",
    val userToken: String = "",
    val isAuthLoading: Boolean = false,
    val authError: String = "",
    val authErrorCode: String = "",
    val retryCount: Int = 0
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val _uiState = MutableStateFlow(PhoneUiState())
    val uiState: StateFlow<PhoneUiState> = _uiState.asStateFlow()

    private var deviceManager: Device? = null
    private var audioCaptureManager: AudioCaptureManager? = null
    private var audioPlaybackManager: AudioPlaybackManager? = null
    private var cameraManager: CameraManager? = null
    internal var aliyunAuthManager: AliyunAuthManager? = null
    private var tokenManager: TokenManager? = null

    private var audioSeq = 0
    private var micStreamId: String? = null
    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.getDefault())
    private val PREFS_NAME = "audio_chat_prefs"
    private val PREF_SERVER_URL = "server_url"
    private val PREF_USER_ID = "user_id"

    init {
        Log.d("MainViewModel", "ViewModel 初始化")

        // 初始化设备 ID（使用 Android ID 生成唯一标识）
        val deviceId = DeviceIdManager.getDeviceId(getApplication())
        _uiState.value = _uiState.value.copy(deviceId = deviceId)
        Log.i("MainViewModel", "设备 ID: $deviceId")

        aliyunAuthManager = AliyunAuthManager(getApplication())
        aliyunAuthManager?.init("NcwKamP+kaqg+OMsxa9Xf1PzcFNXk1UODo8QS2huj0k8YyxVnZziqDSsw+l6m740SfJh7BtFtgoKEQdvpy0WGh5TDDrLPrqfGCuwYHi0/0a1T+lkJX0eN+Nmtve7b2Hnl+3zqEO3DU0is+uhtcJiNDiNCW7dyI9SBEC1G8Eheddl4SVD71Ocx2usjlntoHmy6dnJskoqRzWqowcM3p1Yc27N7zsRi3aLkoIaDTuiYu9laihSryXSM1qdAYfOnGcZZIJADpCULRmsyabQ44vOdqpFT30iBbVCwMB7285G71/5x9Rd7nt4Sw==")

        tokenManager = TokenManager(getApplication())

        // 检查是否已登录
        checkExistingAuth()

        // 如果已登录，自动启动常驻服务（延迟3秒，等待Activity完全初始化）
        // 注意：常驻服务现在在 connect() 中自动启动，这里只做检查
    }

    private fun autoStartIfLoggedIn() {
        // 常驻服务已在 connect() 中通过 ViewModel 管理，无需单独启动
    }
    
    private fun checkExistingAuth() {
        val authInfo = tokenManager?.getAuthInfo()
        if (authInfo != null && tokenManager?.isAccessTokenValid() == true) {
            _uiState.value = _uiState.value.copy(
                isLoggedIn = true,
                userPhone = authInfo.phoneNumber ?: "",
                userId = authInfo.userId,
                userToken = authInfo.accessToken
            )
            addLog("INFO", "检测到已登录用户: ${authInfo.userId}")
            // 自动建立连接
            autoConnect()
        }
    }

    private fun autoConnect() {
        viewModelScope.launch {
            delay(1000) // 等待 ViewModel 完全初始化
            addLog("INFO", "自动建立连接...")
            connect()
        }
    }

    fun updateServerUrl(url: String) {
        _uiState.value = _uiState.value.copy(serverUrl = url)
        addLog("INFO", "Server URL: $url")
    }

    fun updateUserId(userId: String) {
        _uiState.value = _uiState.value.copy(userId = userId)
    }

    fun updateDeviceId(deviceId: String) {
        _uiState.value = _uiState.value.copy(deviceId = deviceId)
    }

    fun connect() {
        viewModelScope.launch {
            try {
                addLog("INFO", "正在连接服务器...")
                val state = _uiState.value

                val localIp = getLocalIpAddress()

                val config = DeviceConfig(
                    serverUrl = state.serverUrl,
                    userId = state.userId,
                    deviceId = state.deviceId,
                    accessToken = state.userToken,
                    deviceName = "android-phone",
                    properties = mapOf(
                        "device_role" to "phone",
                        "endpoint.role.phone" to true,
                        "endpoint.compute.vision" to true,
                        "peer.video.receiver" to true,
                        "audio_chat.audio_input" to "sensor.mic",
                        "audio_chat.audio_output" to "actuator.speaker",
                        "local_ip" to localIp
                    )
                )

                deviceManager = DeviceManager.create(config)
                deviceManager?.setListener(createDeviceListener())
                deviceManager?.setContext(getApplication())

                addEvent("control.device.register", "send", "userId=${state.userId}, deviceId=${state.deviceId}")
                deviceManager?.connect()
                deviceManager?.start()

                addLog("INFO", "连接请求已发送 (IP: $localIp)")
            } catch (e: Exception) {
                addLog("ERROR", "连接失败: ${e.message}")
                Log.e("MainViewModel", "连接失败", e)
            }
        }
    }

    private fun startForegroundService() {
        val state = _uiState.value
        val app = getApplication<Application>()
        val intent = Intent(app, AudioCaptureService::class.java).apply {
            action = AudioCaptureService.ACTION_START
            putExtra(AudioCaptureService.EXTRA_SERVER_URL, state.serverUrl)
            putExtra(AudioCaptureService.EXTRA_USER_ID, state.userId)
            putExtra(AudioCaptureService.EXTRA_DEVICE_ID, state.deviceId)
            putExtra(AudioCaptureService.EXTRA_ACCESS_TOKEN, state.userToken)
            putExtra(AudioCaptureService.EXTRA_DEVICE_NAME, "android-phone")
        }
        app.startForegroundService(intent)
        addLog("INFO", "前台服务已启动")
    }

    private fun stopForegroundService() {
        val app = getApplication<Application>()
        val intent = Intent(app, AudioCaptureService::class.java).apply {
            action = AudioCaptureService.ACTION_STOP
        }
        app.startService(intent)
        addLog("INFO", "前台服务已停止")
    }

    private fun savePrefs() {
        val prefs = getApplication<Application>().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().apply {
            putString(PREF_SERVER_URL, _uiState.value.serverUrl)
            putString(PREF_USER_ID, _uiState.value.userId)
            // deviceId 从 Android ID 生成，不需要保存
            apply()
        }
    }

    private fun loadPrefs(): Pair<String, String>? {
        val prefs = getApplication<Application>().getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val serverUrl = prefs.getString(PREF_SERVER_URL, null)
        val userId = prefs.getString(PREF_USER_ID, null)

        return if (serverUrl != null && userId != null) {
            Pair(serverUrl, userId)
        } else null
    }

    private fun getLocalIpAddress(): String {
        val wifiManager = getApplication<Application>().getSystemService(Context.WIFI_SERVICE) as android.net.wifi.WifiManager
        val wifiInfo = wifiManager.connectionInfo
        val ipAddress = wifiInfo.ipAddress
        return String.format(
            "%d.%d.%d.%d",
            ipAddress and 0xff,
            ipAddress shr 8 and 0xff,
            ipAddress shr 16 and 0xff,
            ipAddress shr 24 and 0xff
        )
    }

    fun disconnect() {
        try {
            stopAudioCapture()
            audioPlaybackManager?.stopPlayback()
            deviceManager?.disconnect()

            _uiState.value = _uiState.value.copy(
                isConnected = false,
                isRegistered = false,
                isControlConnected = false,
                isStreamConnected = false,
                controlState = "idle",
                streamState = "idle",
                isCapturingAudio = false,
                isCameraActive = false,
                isSpeakerActive = false,
                isVibrating = false,
                sessionId = ""
            )

            addLog("INFO", "已断开连接")
        } catch (e: Exception) {
            addLog("ERROR", "断开连接失败: ${e.message}")
        }
    }

    private fun createDeviceListener(): DeviceListener {
        return object : DeviceListener {
            override fun onDeviceRegistered() {
                _uiState.value = _uiState.value.copy(
                    isRegistered = true,
                    isConnected = true,
                    isControlConnected = true,
                    controlState = "connected"
                )
                addLog("INFO", "设备注册成功!")
                addEvent("control.device.registered", "recv", "设备注册成功")
                initializeAudio()
            }

            override fun onStreamConnected() {
                _uiState.value = _uiState.value.copy(
                    isStreamConnected = true,
                    streamState = "connected"
                )
                addLog("INFO", "流连接已建立")
                addEvent("stream.connected", "recv", "流连接已建立")
            }

            override fun onHeartbeatReceived() {
                _uiState.value = _uiState.value.copy(lastHeartbeatTime = System.currentTimeMillis())
                addEvent("control.device.heartbeat.sent", "send", "")
            }

            override fun onRgbCaptureRequest(requestId: String) {
                handleRgbCaptureRequest(requestId)
            }

            override fun onCommandReceived(event: AudioChatEvent) {
                handleCommand(event)
            }

            override fun onAudioOutputChunk(chunk: StreamChunk) {
                handleAudioOutput(chunk)
            }

            override fun onEvent(eventName: String, detail: String) {
                addEvent(eventName, "recv", detail)
                val s = _uiState.value
                _uiState.value = s.copy(
                    eventsReceived = s.eventsReceived + 1,
                    controlEventsCount = if (eventName.startsWith("control.")) s.controlEventsCount + 1 else s.controlEventsCount,
                    streamEventsCount = if (eventName.startsWith("stream.")) s.streamEventsCount + 1 else s.streamEventsCount,
                    commandEventsCount = if (eventName.startsWith("command.")) s.commandEventsCount + 1 else s.commandEventsCount
                )
            }

            override fun onRawMessage(direction: String, message: String) {
                val prefix = if (direction == "send") ">>> " else "<<< "
                val newMessages = (_uiState.value.rawMessages + "$prefix$message").takeLast(50)
                _uiState.value = _uiState.value.copy(rawMessages = newMessages)
            }

            override fun onReconnectNeeded() {
                addLog("WARN", "连接断开, 正在重连...")
                viewModelScope.launch {
                    try {
                        deviceManager?.disconnect()
                        delay(1000)
                        addEvent("control.device.register", "send", "重连")
                        deviceManager?.connect()
                        deviceManager?.start()
                        addLog("INFO", "重连完成")
                    } catch (e: Exception) {
                        addLog("ERROR", "重连失败: ${e.message}")
                    }
                }
            }

            override fun onYoloModelLoaded(loaded: Boolean) {
                _uiState.value = _uiState.value.copy(yoloModelLoaded = loaded)
                addLog("INFO", "YOLO模型${if (loaded) "加载成功" else "加载失败"}")
            }

            override fun onPeerVideoFrame(frameResult: Map<String, Any>) {
                val detections = frameResult["detections"] as? List<*> ?: emptyList<Any>()
                _uiState.value = _uiState.value.copy(
                    framesProcessed = _uiState.value.framesProcessed + 1,
                    yoloInferenceTimeMs = (frameResult["inferenceTimeMs"] as? Number)?.toLong() ?: 0L,
                    yoloLastDetectionCount = detections.size
                )
            }

            override fun onPeerVideoTaskCompleted(result: Map<String, Any>) {
                addLog("INFO", "Peer video 任务完成: $result")
                val found = result["found"] as? Boolean ?: false
                if (found) {
                    _uiState.value = _uiState.value.copy(
                        objectsFound = _uiState.value.objectsFound + 1
                    )
                }
            }

            override fun onPeerVideoClientConnected(clientIp: String) {
                addLog("INFO", "眼镜直连已连接: $clientIp")
                _uiState.value = _uiState.value.copy(
                    isPeerVideoConnected = true,
                    peerVideoClientIp = clientIp,
                    showPeerVideoToast = true,
                    peerVideoToastMessage = "眼镜直连已连接"
                )
                addEvent("peer.video.connected", "recv", "clientIp=$clientIp")
            }

            override fun onPeerVideoClientDisconnected() {
                addLog("INFO", "眼镜直连已断开")
                _uiState.value = _uiState.value.copy(
                    isPeerVideoConnected = false,
                    peerVideoClientIp = "",
                    showPeerVideoToast = true,
                    peerVideoToastMessage = "眼镜直连已断开"
                )
                addEvent("peer.video.disconnected", "recv", "")
            }
        }
    }

    fun dismissPeerVideoToast() {
        _uiState.value = _uiState.value.copy(
            showPeerVideoToast = false,
            peerVideoToastMessage = ""
        )
    }

    private fun initializeAudio() {
        addLog("INFO", "音频模块初始化完成")
    }

    private fun handleRgbCaptureRequest(requestId: String) {
        addLog("INFO", "收到 RGB 采集请求: $requestId")
        addEvent("command.rgb.capture.requested", "recv", "requestId=$requestId")

        viewModelScope.launch {
            try {
                addLog("WARN", "拍照功能需要实现 CameraManager")
                _uiState.value = _uiState.value.copy(
                    captureFailCount = _uiState.value.captureFailCount + 1,
                    lastCaptureResult = "失败: CameraManager 未实现"
                )
            } catch (e: Exception) {
                addLog("ERROR", "拍照失败: ${e.message}")
                _uiState.value = _uiState.value.copy(
                    captureFailCount = _uiState.value.captureFailCount + 1,
                    lastCaptureResult = "失败: ${e.message}"
                )
            }
        }
    }

    private fun handleCommand(event: AudioChatEvent) {
        val command = event.payload["command"] as? String ?: ""
        val params = event.payload["params"] as? Map<*, *> ?: emptyMap<Any, Any>()

        addLog("INFO", "收到命令: $command")
        addEvent("command.$command.received", "recv", "params=$params")

        when (command) {
            "vibrate" -> {
                _uiState.value = _uiState.value.copy(isVibrating = true)
                addLog("INFO", "执行震动")
                sendCommandResponse(event, completed = true)
                viewModelScope.launch {
                    kotlinx.coroutines.delay(500)
                    _uiState.value = _uiState.value.copy(isVibrating = false)
                }
            }
            else -> {
                addLog("WARN", "未知命令: $command")
                sendCommandResponse(event, failed = true, message = "不支持的命令: $command")
            }
        }
    }

    private fun sendCommandResponse(
        originalEvent: AudioChatEvent,
        completed: Boolean = false,
        failed: Boolean = false,
        message: String = ""
    ) {
        val state = _uiState.value
        val response = when {
            failed -> AudioChatEvent.createCommandFailedEvent(
                userId = state.userId,
                deviceId = state.deviceId,
                message = message
            )
            else -> AudioChatEvent.createCommandCompletedEvent(
                userId = state.userId,
                deviceId = state.deviceId,
                result = mapOf("status" to "completed", "message" to message)
            )
        }

        deviceManager?.sendCommandResponse(response)
        addEvent(
            if (failed) "command.failed" else "command.completed",
            "send",
            message
        )
    }

    private fun handleAudioOutput(chunk: StreamChunk) {
        _uiState.value = _uiState.value.copy(
            audioChunksPlayed = _uiState.value.audioChunksPlayed + 1,
            isSpeakerActive = true
        )
    }

    fun startAudioCapture() {
        addLog("INFO", "开始音频采集...")
        _uiState.value = _uiState.value.copy(isCapturingAudio = true)
        addEvent("stream.input.opened", "send", "sensor.mic 开始采集")
    }

    fun stopAudioCapture() {
        audioCaptureManager?.stopCapture()
        _uiState.value = _uiState.value.copy(isCapturingAudio = false)
        addLog("INFO", "停止音频采集")
        addEvent("stream.input.closed", "send", "sensor.mic 停止采集")
    }

    fun startAudioPlayback() {
        addLog("INFO", "开始音频播放...")
        _uiState.value = _uiState.value.copy(isSpeakerActive = true)
        addEvent("stream.output.opened", "send", "actuator.speaker 开始播放")
    }

    fun stopAudioPlayback() {
        audioPlaybackManager?.stopPlayback()
        _uiState.value = _uiState.value.copy(isSpeakerActive = false)
        addLog("INFO", "停止音频播放")
        addEvent("stream.output.closed", "send", "actuator.speaker 停止播放")
    }

    fun manualCapturePhoto() {
        addLog("INFO", "手动拍照...")
        addEvent("command.rgb.capture.manual", "send", "手动触发拍照")
        handleRgbCaptureRequest(AudioChatEvent.newId())
    }

    fun processSelectedImage(uri: Uri) {
        addLog("INFO", "处理选择的图片...")
        _uiState.value = _uiState.value.copy(selectedImageUri = uri)
        viewModelScope.launch {
            try {
                val context = getApplication<Application>()
                val inputStream = context.contentResolver.openInputStream(uri)
                val bitmap = BitmapFactory.decodeStream(inputStream)
                inputStream?.close()

                if (bitmap == null) {
                    _uiState.value = _uiState.value.copy(
                        lastImageProcessResult = "失败: 无法解析图片"
                    )
                    return@launch
                }

                val modelName = _uiState.value.yoloModelName

                // 使用 YOLO 检测
                val yoloDetector = YoloDetector(context)
                val modelInfo = com.audiochat.phone.vision.VisionModels.findByName(modelName)
                val loaded = yoloDetector.loadModel(modelName, modelInfo?.inputSize ?: 320)

                if (!loaded) {
                    _uiState.value = _uiState.value.copy(
                        lastImageProcessResult = "失败: YOLO模型未加载"
                    )
                    return@launch
                }

                val result = yoloDetector.detect(bitmap, 0.3f)
                yoloDetector.close()

                val detectionCount = result.detections.size
                val inferenceTime = result.inferenceTimeMs

                // 保存标注后的图片
                val annotatedBitmap = result.annotatedBitmap
                val cacheDir = context.cacheDir
                val annotatedFile = File(cacheDir, "yolo_annotated_${System.currentTimeMillis()}.jpg")
                FileOutputStream(annotatedFile).use { fos ->
                    annotatedBitmap.compress(Bitmap.CompressFormat.JPEG, 90, fos)
                }
                val annotatedUri = androidx.core.content.FileProvider.getUriForFile(
                    context,
                    "${context.packageName}.fileprovider",
                    annotatedFile
                )

                _uiState.value = _uiState.value.copy(
                    lastImageProcessResult = "成功: 检测到${detectionCount}个物体 (${inferenceTime}ms)",
                    currentDetections = result.detections,
                    yoloInferenceTimeMs = inferenceTime,
                    yoloLastDetectionCount = detectionCount,
                    framesProcessed = _uiState.value.framesProcessed + 1,
                    annotatedImageUri = annotatedUri
                )

                addLog("INFO", "图片YOLO检测完成: ${detectionCount}个物体, ${inferenceTime}ms")

            } catch (e: Exception) {
                addLog("ERROR", "图片处理失败: ${e.message}")
                _uiState.value = _uiState.value.copy(
                    lastImageProcessResult = "失败: ${e.message}"
                )
            }
        }
    }

    fun sendWakeEvent() {
        addLog("INFO", "发送模拟唤醒事件")
        addEvent("control.device.wake.requested", "send", "模拟唤醒")
        deviceManager?.sendEvent(
            AudioChatEvent(
                event_name = "control.device.wake.requested",
                user_id = _uiState.value.userId,
                producer_id = _uiState.value.deviceId,
                payload = mapOf("source" to "manual_debug")
            )
        )
    }

    fun sendInterruptEvent() {
        addLog("INFO", "发送模拟打断事件")
        addEvent("control.device.interrupt.requested", "send", "模拟打断")
        deviceManager?.sendEvent(
            AudioChatEvent(
                event_name = "control.device.interrupt.requested",
                user_id = _uiState.value.userId,
                producer_id = _uiState.value.deviceId,
                payload = mapOf("source" to "manual_debug")
            )
        )
    }

    fun sendCloseSessionEvent() {
        addLog("INFO", "发送结束对话事件")
        addEvent("control.session.close.requested", "send", "结束对话")
        deviceManager?.sendEvent(
            AudioChatEvent(
                event_name = "control.session.close.requested",
                user_id = _uiState.value.userId,
                producer_id = _uiState.value.deviceId,
                payload = mapOf("source" to "manual_debug")
            )
        )
    }

    fun testVibrate() {
        addLog("INFO", "测试震动")
        _uiState.value = _uiState.value.copy(isVibrating = true)
        viewModelScope.launch {
            kotlinx.coroutines.delay(500)
            _uiState.value = _uiState.value.copy(isVibrating = false)
        }
    }

    fun testSpeaker() {
        addLog("INFO", "测试扬声器")
        _uiState.value = _uiState.value.copy(isSpeakerActive = true)
        viewModelScope.launch {
            kotlinx.coroutines.delay(1000)
            _uiState.value = _uiState.value.copy(isSpeakerActive = false)
        }
    }

    fun stopPeerVideoTask() {
        addLog("INFO", "停止 Peer Video 任务")
        _uiState.value = _uiState.value.copy(
            currentFrame = null,
            peerVideoTaskState = null,
            currentDetections = emptyList()
        )
    }

    /**
     * 切换视觉模型
     */
    fun switchVisionModel(modelName: String) {
        addLog("INFO", "切换视觉模型: $modelName")
        _uiState.value = _uiState.value.copy(yoloModelName = modelName)
    }

    fun sendCustomEvent(eventName: String, payload: String) {
        if (eventName.isBlank()) return
        addLog("INFO", "发送自定义事件: $eventName")
        addEvent(eventName, "send", payload)

        try {
            val payloadMap = if (payload.isNotBlank()) {
                @Suppress("UNCHECKED_CAST")
                org.json.JSONObject(payload).toMap() as Map<String, Any>
            } else {
                emptyMap()
            }

            deviceManager?.sendEvent(
                AudioChatEvent(
                    event_name = eventName,
                    user_id = _uiState.value.userId,
                    producer_id = _uiState.value.deviceId,
                    payload = payloadMap
                )
            )
        } catch (e: Exception) {
            addLog("ERROR", "发送自定义事件失败: ${e.message}")
        }
    }

    fun sendPresetEvent(eventName: String) {
        sendCustomEvent(eventName, "{}")
    }

    fun setEventFilter(filter: String) {
        _uiState.value = _uiState.value.copy(eventFilter = filter).let { state ->
            state.copy(
                filteredEvents = when (filter) {
                    "control" -> state.allEvents.filter { it.eventName.startsWith("control.") }
                    "stream" -> state.allEvents.filter { it.eventName.startsWith("stream.") }
                    "command" -> state.allEvents.filter { it.eventName.startsWith("command.") }
                    else -> state.allEvents
                }
            )
        }
    }

    fun clearEvents() {
        _uiState.value = _uiState.value.copy(
            allEvents = emptyList(),
            filteredEvents = emptyList(),
            eventsReceived = 0,
            controlEventsCount = 0,
            streamEventsCount = 0,
            commandEventsCount = 0
        )
    }

    fun setLogLevelFilter(level: String) {
        _uiState.value = _uiState.value.copy(logLevelFilter = level).let { state ->
            state.copy(
                filteredLogEntries = when (level) {
                    "ALL" -> state.logEntries
                    else -> state.logEntries.filter { it.level == level }
                }
            )
        }
    }

    fun clearLogs() {
        _uiState.value = _uiState.value.copy(
            logEntries = emptyList(),
            filteredLogEntries = emptyList()
        )
    }

    fun updateAudioParams(micRate: String, speakerRate: String, chunkMs: String) {
        addLog("INFO", "更新音频参数: mic=$micRate, speaker=$speakerRate, chunk=$chunkMs ms")
    }

    fun updateImageParams(maxSizeKB: String, quality: String, maxWidth: String) {
        addLog("INFO", "更新图片参数: maxSize=$maxSizeKB KB, quality=$quality, maxWidth=$maxWidth")
        _uiState.value = _uiState.value.copy(
            jpegQuality = quality.toIntOrNull() ?: 90
        )
    }

    fun startOneClickAuth() {
        if (!isNetworkAvailable()) {
            _uiState.value = _uiState.value.copy(
                authError = "网络不可用，请检查网络连接",
                authErrorCode = "NETWORK_UNAVAILABLE"
            )
            addLog("ERROR", "网络不可用")
            return
        }

        addLog("INFO", "准备一键登录")
        _uiState.value = _uiState.value.copy(
            authError = "",
            authErrorCode = "",
            isAuthLoading = false
        )
    }

    fun onAuthSuccess(token: String) {
        Log.i("MainViewModel", "onAuthSuccess called with token length: ${token.length}")
        addLog("INFO", "获取 Token 成功，正在取号")
        
        _uiState.value = _uiState.value.copy(
            isAuthLoading = true,
            authError = "",
            authErrorCode = ""
        )
        
        getMobileFromServer(token, 0)
    }

    fun onAuthFailed(message: String) {
        val errorCode = parseErrorCode(message)
        val error = ErrorHandler.handleAuthError(errorCode, message)
        
        addLog("ERROR", error.internalMessage)
        
        if (error.shouldShowToUser) {
            _uiState.value = _uiState.value.copy(
                authError = error.userMessage,
                authErrorCode = error.code,
                isAuthLoading = false
            )
        } else {
            _uiState.value = _uiState.value.copy(
                authError = "登录失败，请重试",
                authErrorCode = error.code,
                isAuthLoading = false
            )
        }
        
        aliyunAuthManager?.hideLoginLoading()
    }

    private fun getMobileFromServer(token: String, retryCount: Int) {
        Log.i("MainViewModel", ">>> getMobileFromServer ENTER token长度: ${token.length}, retry: $retryCount")
        addLog("INFO", "正在取号... (尝试 ${retryCount + 1}/3)")

        val mainHandler = Handler(Looper.getMainLooper())
        val serverUrl = _uiState.value.serverUrl
        val url = "$serverUrl/api/auth/get-mobile"

        val client = okhttp3.OkHttpClient.Builder()
            .connectTimeout(15, java.util.concurrent.TimeUnit.SECONDS)
            .readTimeout(15, java.util.concurrent.TimeUnit.SECONDS)
            .writeTimeout(15, java.util.concurrent.TimeUnit.SECONDS)
            .build()

        val json = """
            {
                "token": "$token"
            }
        """.trimIndent()

        val mediaType = "application/json; charset=utf-8".toMediaType()
        val body = json.toRequestBody(mediaType)

        val request = okhttp3.Request.Builder()
            .url(url)
            .post(body)
            .build()

        client.newCall(request).enqueue(object : okhttp3.Callback {
            override fun onFailure(call: okhttp3.Call, e: IOException) {
                Log.e("MainViewModel", ">>> onFailure: ${e.message}", e)
                
                mainHandler.post {
                    if (retryCount < 2) {
                        addLog("WARN", "请求失败，准备重试 (${retryCount + 1}/3): ${e.message}")
                        _uiState.value = _uiState.value.copy(
                            retryCount = retryCount + 1
                        )
                        
                        mainHandler.postDelayed({
                            getMobileFromServer(token, retryCount + 1)
                        }, 2000)
                    } else {
                        val error = ErrorHandler.handleNetworkError(e)
                        addLog("ERROR", error.internalMessage)
                        
                        _uiState.value = _uiState.value.copy(
                            authError = error.userMessage,
                            authErrorCode = error.code,
                            isAuthLoading = false,
                            retryCount = 0
                        )
                        aliyunAuthManager?.hideLoginLoading()
                        aliyunAuthManager?.quitLoginPage()
                    }
                }
            }

            override fun onResponse(call: okhttp3.Call, response: Response) {
                Log.i("MainViewModel", ">>> onResponse: code=${response.code}")
                
                if (!response.isSuccessful) {
                    mainHandler.post {
                        val error = ErrorHandler.handleHttpError(response.code, "服务器错误")
                        addLog("ERROR", error.internalMessage)
                        
                        _uiState.value = _uiState.value.copy(
                            authError = error.userMessage,
                            authErrorCode = error.code,
                            isAuthLoading = false,
                            retryCount = 0
                        )
                        aliyunAuthManager?.hideLoginLoading()
                        aliyunAuthManager?.quitLoginPage()
                    }
                    return
                }
                
                response.body?.string()?.let { responseBody ->
                    Log.i("MainViewModel", ">>> responseBody: $responseBody")
                    try {
                        val jsonObject = org.json.JSONObject(responseBody)
                        val success = jsonObject.optBoolean("success", false)
                        val phoneNumber = jsonObject.optString("phone_number", "")
                        val userId = jsonObject.optString("user_id", "")
                        val message = jsonObject.optString("message", "")
                        val accessToken = jsonObject.optString("access_token", "")
                        val refreshToken = jsonObject.optString("refresh_token", "")
                        val expiresIn = jsonObject.optInt("expires_in", 0)

                        mainHandler.post {
                            if (success) {
                                // 保存认证信息
                                tokenManager?.saveAuthInfo(
                                    accessToken = accessToken,
                                    refreshToken = refreshToken,
                                    expiresIn = expiresIn,
                                    userId = userId,
                                    phoneNumber = phoneNumber
                                )
                                
                                _uiState.value = _uiState.value.copy(
                                    isLoggedIn = true,
                                    userPhone = phoneNumber,
                                    userId = userId,
                                    userToken = accessToken,
                                    isAuthLoading = false,
                                    authError = "",
                                    authErrorCode = "",
                                    retryCount = 0
                                )
                                addLog("INFO", "一键登录成功: $phoneNumber")
                                Log.i("MainViewModel", ">>> UI updated: isLoggedIn=true")
                                aliyunAuthManager?.hideLoginLoading()
                                aliyunAuthManager?.quitLoginPage()
                                
                                // 自动建立WebSocket连接
                                addLog("INFO", "正在自动建立WebSocket连接...")
                                connect()
                            } else {
                                val error = ErrorHandler.handleServerError(message)
                                addLog("ERROR", error.internalMessage)
                                
                                if (error.shouldShowToUser) {
                                    _uiState.value = _uiState.value.copy(
                                        authError = error.userMessage,
                                        authErrorCode = error.code,
                                        isAuthLoading = false,
                                        retryCount = 0
                                    )
                                } else {
                                    _uiState.value = _uiState.value.copy(
                                        authError = "登录失败，请重试",
                                        authErrorCode = error.code,
                                        isAuthLoading = false,
                                        retryCount = 0
                                    )
                                }
                                aliyunAuthManager?.hideLoginLoading()
                                aliyunAuthManager?.quitLoginPage()
                            }
                        }
                    } catch (e: Exception) {
                        Log.e("MainViewModel", ">>> 解析响应失败", e)
                        mainHandler.post {
                            val error = ErrorHandler.handleAuthError("PARSE_ERROR", e.message ?: "解析失败")
                            addLog("ERROR", error.internalMessage)
                            
                            _uiState.value = _uiState.value.copy(
                                authError = error.userMessage,
                                authErrorCode = error.code,
                                isAuthLoading = false,
                                retryCount = 0
                            )
                            addLog("ERROR", "解析失败: ${e.message}")
                            aliyunAuthManager?.hideLoginLoading()
                            aliyunAuthManager?.quitLoginPage()
                        }
                    }
                }
            }
        })
    }

    private fun isNetworkAvailable(): Boolean {
        val connectivityManager = getApplication<Application>().getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = connectivityManager.activeNetwork ?: return false
        val capabilities = connectivityManager.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
               capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }

    private fun parseErrorCode(message: String): String {
        return when {
            message.contains("600017", ignoreCase = true) -> "APPID_SECRET_ERROR"
            message.contains("600000", ignoreCase = true) -> "SUCCESS"
            message.contains("600001", ignoreCase = true) -> "AUTH_PAGE_SUCCESS"
            message.contains("600002", ignoreCase = true) -> "AUTH_PAGE_FAILED"
            message.contains("600004", ignoreCase = true) -> "TOKEN_FAILED"
            message.contains("600005", ignoreCase = true) -> "CARRIER_FAILED"
            message.contains("600006", ignoreCase = true) -> "VENDOR_FAILED"
            message.contains("600007", ignoreCase = true) -> "NETWORK_FAILED"
            message.contains("600008", ignoreCase = true) -> "TIMEOUT"
            else -> "UNKNOWN_ERROR"
        }
    }

    fun clearAuthError() {
        _uiState.value = _uiState.value.copy(
            authError = "",
            authErrorCode = ""
        )
    }

    fun logout() {
        addLog("INFO", "退出登录")
        
        // 清除token
        tokenManager?.clearAuthInfo()
        
        _uiState.value = _uiState.value.copy(
            isLoggedIn = false,
            userPhone = "",
            userToken = ""
        )
        disconnect()
        aliyunAuthManager?.release()
        aliyunAuthManager?.init("NcwKamP+kaqg+OMsxa9Xf1PzcFNXk1UODo8QS2huj0k8YyxVnZziqDSsw+l6m740SfJh7BtFtgoKEQdvpy0WGh5TDDrLPrqfGCuwYHi0/0a1T+lkJX0eN+Nmtve7b2Hnl+3zqEO3DU0is+uhtcJiNDiNCW7dyI9SBEC1G8Eheddl4SVD71Ocx2usjlntoHmy6dnJskoqRzWqowcM3p1Yc27N7zsRi3aLkoIaDTuiYu9laihSryXSM1qdAYfOnGcZZIJADpCULRmsyabQ44vOdqpFT30iBbVCwMB7285G71/5x9Rd7nt4Sw==")
    }

    private fun addEvent(eventName: String, direction: String, detail: String) {
        val entry = EventLogEntry(
            timestamp = timeFormat.format(Date()),
            eventName = eventName,
            direction = direction,
            detail = detail
        )
        val newAll = (_uiState.value.allEvents + entry).takeLast(200)
        _uiState.value = _uiState.value.copy(allEvents = newAll)
        setEventFilter(_uiState.value.eventFilter)
    }

    private fun addLog(level: String, message: String) {
        val entry = LogEntry(
            timestamp = timeFormat.format(Date()),
            level = level,
            message = message
        )
        val newLogs = (_uiState.value.logEntries + entry).takeLast(500)
        _uiState.value = _uiState.value.copy(logEntries = newLogs)
        setLogLevelFilter(_uiState.value.logLevelFilter)
    }

    override fun onCleared() {
        super.onCleared()
        disconnect()
        audioCaptureManager?.release()
        audioPlaybackManager?.release()
        cameraManager?.release()
        aliyunAuthManager?.release()
    }
}

private fun org.json.JSONObject.toMap(): Map<String, Any> {
    val map = mutableMapOf<String, Any>()
    keys().forEach { key ->
        map[key] = get(key)
    }
    return map
}