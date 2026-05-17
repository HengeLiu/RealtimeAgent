package com.audiochat.phone.video

import android.content.Context
import android.graphics.Bitmap
import android.net.wifi.WifiManager
import android.util.Log
import com.audiochat.phone.device.DeviceManager
import com.audiochat.phone.protocol.AudioChatEvent
import com.audiochat.phone.vision.TrafficLightState
import com.audiochat.phone.vision.YoloDetector
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Peer Video Task Manager
 * 管理 peer video receiver 的完整流程：
 * 1. 接收 peer.video.receiver.start 命令
 * 2. 启动 WebSocket server
 * 3. 接收视频帧
 * 4. 运行 YOLO 检测
 * 5. 发送状态回报和结果
 */
class PeerVideoTaskManager(
    private val context: Context,
    private val deviceManager: DeviceManager,
    private val scope: CoroutineScope = CoroutineScope(Dispatchers.Default)
) {
    companion object {
        private const val TAG = "PeerVideoTaskManager"
        private const val DEFAULT_PORT = 19081
        private const val DEFAULT_TIMEOUT_SECONDS = 30f
    }

    data class TaskState(
        val peerSessionId: String,
        val taskType: String,
        val purpose: String,
        val objectName: String = "",
        val timeoutSeconds: Float = DEFAULT_TIMEOUT_SECONDS.toFloat(),
        val startTime: Long = System.currentTimeMillis()
    )

    data class FrameResult(
        val bitmap: Bitmap,
        val annotatedBitmap: Bitmap,
        val detections: List<YoloDetector.Detection>,
        val frameSeq: Long,
        val inferenceTimeMs: Long
    )

    private var receiverServer: PeerVideoReceiverServer? = null
    private var yoloDetector: YoloDetector? = null
    private var currentTask: TaskState? = null
    private var frameProcessingJob: Job? = null
    private var timeoutJob: Job? = null
    private val isRunning = AtomicBoolean(false)

    private val _frameResultFlow = MutableSharedFlow<FrameResult>(extraBufferCapacity = 16)
    val frameResultFlow: SharedFlow<FrameResult> = _frameResultFlow

    private val _taskStateFlow = MutableSharedFlow<TaskState?>(replay = 1)
    val taskStateFlow: SharedFlow<TaskState?> = _taskStateFlow

    var onFrameProcessed: ((FrameResult) -> Unit)? = null
    var onTaskCompleted: ((Map<String, Any>) -> Unit)? = null
    var onPeerConnected: ((String) -> Unit)? = null
    var onPeerDisconnected: (() -> Unit)? = null

    /**
     * YOLO 检测器是否已加载
     */
    val isDetectorReady: Boolean
        get() = yoloDetector?.isReady == true

    /**
     * 初始化 YOLO 检测器
     */
    fun initialize(): Boolean {
        yoloDetector = YoloDetector(context)

        val loaded = try {
            yoloDetector?.loadModel("yolov8n", 320) ?: false
        } catch (e: Exception) {
            Log.w(TAG, "NCNN model not found, using mock mode: ${e.message}")
            false
        }

        if (loaded) {
            Log.i(TAG, "NCNN YOLO model loaded successfully")
        } else {
            Log.i(TAG, "Running in mock mode (no model)")
        }

        receiverServer = PeerVideoReceiverServer(DEFAULT_PORT, scope)
        setupReceiverCallbacks()

        return true
    }

    /**
     * 处理 peer.video.receiver.start 命令
     */
    fun handleReceiverStartCommand(event: AudioChatEvent): Boolean {
        val payload = event.payload
        val commandId = event.command_id ?: return false

        Log.i(TAG, "Received peer.video.receiver.start: $payload")

        val peerSessionId = payload["peer_session_id"] as? String ?: return false
        val taskType = payload["task_type"] as? String ?: "unknown"
        val purpose = payload["purpose"] as? String ?: ""
        val objectName = (payload["object_name"] as? String) ?: ""
        val timeoutSeconds = (payload["timeout_seconds"] as? Number)?.toFloat() ?: DEFAULT_TIMEOUT_SECONDS
        val mediaConfig = payload["media_config"] as? Map<*, *>

        if (isRunning.get()) {
            sendCommandFailed(commandId, "Another task is already running")
            return false
        }

        currentTask = TaskState(
            peerSessionId = peerSessionId,
            taskType = taskType,
            purpose = purpose,
            objectName = objectName,
            timeoutSeconds = timeoutSeconds
        )

        isRunning.set(true)
        _taskStateFlow.tryEmit(currentTask)

        sendCommandAccepted(commandId)

        scope.launch {
            try {
                val localIp = getLocalIpAddress()
                val wsUrl = receiverServer?.getLocalWebSocketUrl(peerSessionId, localIp) ?: ""

                receiverServer?.start(peerSessionId)

                delay(500)

                sendReceiverReady(commandId, wsUrl)

                startFrameProcessing()

                startTimeoutTimer(timeoutSeconds)

                Log.i(TAG, "Peer video receiver started: sessionId=$peerSessionId, wsUrl=$wsUrl")

            } catch (e: Exception) {
                Log.e(TAG, "Failed to start receiver", e)
                sendCommandFailed(commandId, e.message ?: "Failed to start receiver")
                stopTask()
            }
        }

        return true
    }

    /**
     * 处理 peer.video.receiver.stop 命令
     */
    fun handleReceiverStopCommand(event: AudioChatEvent) {
        val commandId = event.command_id ?: return
        Log.i(TAG, "Received peer.video.receiver.stop")

        sendCommandAccepted(commandId)
        stopTask()
        sendCommandCompleted(commandId, mapOf("reason" to "stopped"))
    }

    /**
     * 设置接收器回调
     */
    private fun setupReceiverCallbacks() {
        receiverServer?.onFrameReceived = { frame ->
            processFrame(frame)
        }

        receiverServer?.onClientConnected = { clientIp ->
            Log.i(TAG, "Glass connected: $clientIp")
            onPeerConnected?.invoke(clientIp)
            currentTask?.let { task ->
                sendCommandProgress(
                    task.peerSessionId,
                    mapOf(
                        "status" to "peer.sender.connected",
                        "client_ip" to clientIp
                    )
                )
            }
        }

        receiverServer?.onClientDisconnected = {
            Log.i(TAG, "Glass disconnected")
            onPeerDisconnected?.invoke()
        }

        receiverServer?.onError = { message ->
            Log.e(TAG, "Receiver error: $message")
            currentTask?.let { task ->
                sendCommandFailed(task.peerSessionId, message)
            }
        }
    }

    /**
     * 处理视频帧
     */
    private fun processFrame(frame: PeerVideoReceiverServer.VideoFrame) {
        val task = currentTask ?: return

        scope.launch {
            try {
                val result = withContext(Dispatchers.Default) {
                    yoloDetector?.detect(frame.bitmap, 0.3f)
                }

                if (result != null) {
                    val frameResult = FrameResult(
                        bitmap = frame.bitmap,
                        annotatedBitmap = result.annotatedBitmap,
                        detections = result.detections,
                        frameSeq = frame.seq,
                        inferenceTimeMs = result.inferenceTimeMs
                    )

                    _frameResultFlow.emit(frameResult)
                    onFrameProcessed?.invoke(frameResult)

                    if (task.taskType == "find_object_task" && task.objectName.isNotEmpty()) {
                        val found = result.detections.firstOrNull { det ->
                            det.className.equals(task.objectName, ignoreCase = true) ||
                            det.chineseName.contains(task.objectName, ignoreCase = true)
                        }

                        if (found != null) {
                            Log.i(TAG, "Object found: ${found.className}")
                            completeFindObjectTask(task, found)
                        }
                    }

                    if (task.taskType == "traffic_light_task") {
                        val trafficLight = result.detections.firstOrNull { 
                            it.className == "traffic light" 
                        }
                        
                        if (trafficLight != null) {
                            val state = yoloDetector?.detectTrafficLight(frame.bitmap)
                            if (state != null && state != TrafficLightState.UNKNOWN) {
                                Log.i(TAG, "Traffic light detected: $state")
                                completeTrafficLightTask(task, state)
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "Error processing frame", e)
            }
        }
    }

    /**
     * 启动帧处理
     */
    private fun startFrameProcessing() {
        frameProcessingJob?.cancel()
        frameProcessingJob = scope.launch {
            receiverServer?.frameFlow?.collect { frame ->
                processFrame(frame)
            }
        }
    }

    /**
     * 启动超时定时器
     */
    private fun startTimeoutTimer(timeoutSeconds: Float) {
        timeoutJob?.cancel()
        timeoutJob = scope.launch {
            delay((timeoutSeconds * 1000).toLong())
            
            if (isRunning.get()) {
                Log.i(TAG, "Task timeout")
                currentTask?.let { task ->
                    when (task.taskType) {
                        "find_object_task" -> {
                            completeFindObjectTask(task, null)
                        }
                        "traffic_light_task" -> {
                            completeTrafficLightTask(task, TrafficLightState.UNKNOWN)
                        }
                    }
                }
            }
        }
    }

    /**
     * 完成找物任务
     */
    private fun completeFindObjectTask(task: TaskState, detection: YoloDetector.Detection?) {
        val result = if (detection != null) {
            mapOf(
                "type" to "find_object",
                "object_name" to task.objectName,
                "found" to true,
                "confidence" to detection.confidence,
                "message" to "已找到${task.objectName}，位于${getPositionDescription(detection.boundingBox)}",
                "source" to if (yoloDetector?.isReady == true) "yoloe" else "mock"
            )
        } else {
            mapOf(
                "type" to "find_object",
                "object_name" to task.objectName,
                "found" to false,
                "message" to "暂时没有找到${task.objectName}",
                "source" to if (yoloDetector?.isReady == true) "yoloe" else "mock"
            )
        }

        sendCommandCompleted(task.peerSessionId, result)
        onTaskCompleted?.invoke(result)
        stopTask()
    }

    /**
     * 完成红绿灯任务
     */
    private fun completeTrafficLightTask(task: TaskState, state: TrafficLightState) {
        val result = mapOf(
            "type" to "traffic_light",
            "state" to state.name.lowercase(),
            "can_cross" to state.canCross,
            "message" to when (state) {
                TrafficLightState.GREEN -> "绿灯，可以在确认安全后通行"
                TrafficLightState.RED -> "红灯，请等待"
                TrafficLightState.YELLOW -> "黄灯，即将变灯"
                TrafficLightState.UNKNOWN -> "无法识别红绿灯状态"
            },
            "source" to if (yoloDetector?.isReady == true) "yolo" else "mock"
        )

        sendCommandCompleted(task.peerSessionId, result)
        onTaskCompleted?.invoke(result)
        stopTask()
    }

    /**
     * 获取位置描述
     */
    private fun getPositionDescription(box: android.graphics.RectF): String {
        val centerX = box.centerX()
        return when {
            centerX < 0.33f -> "画面左侧"
            centerX > 0.67f -> "画面右侧"
            else -> "画面中间"
        }
    }

    /**
     * 停止任务
     */
    fun stopTask() {
        isRunning.set(false)
        frameProcessingJob?.cancel()
        frameProcessingJob = null
        timeoutJob?.cancel()
        timeoutJob = null
        receiverServer?.stop()
        currentTask = null
        _taskStateFlow.tryEmit(null)
        Log.i(TAG, "Task stopped")
    }

    /**
     * 发送命令接受
     */
    private fun sendCommandAccepted(commandId: String) {
        val event = AudioChatEvent(
            event_name = "command.accepted",
            user_id = deviceManager.userId,
            producer_id = deviceManager.deviceId,
            command_id = commandId,
            payload = mapOf("command_id" to commandId)
        )
        deviceManager.sendEvent(event)
    }

    /**
     * 发送 receiver ready
     */
    private fun sendReceiverReady(commandId: String, wsUrl: String) {
        val event = AudioChatEvent(
            event_name = "command.progress",
            user_id = deviceManager.userId,
            producer_id = deviceManager.deviceId,
            command_id = commandId,
            payload = mapOf(
                "status" to "peer.receiver.ready",
                "receiver" to mapOf(
                    "ws_url" to wsUrl
                )
            )
        )
        deviceManager.sendEvent(event)
    }

    /**
     * 发送命令进度
     */
    private fun sendCommandProgress(commandId: String, data: Map<String, Any>) {
        val event = AudioChatEvent(
            event_name = "command.progress",
            user_id = deviceManager.userId,
            producer_id = deviceManager.deviceId,
            command_id = commandId,
            payload = data
        )
        deviceManager.sendEvent(event)
    }

    /**
     * 发送命令完成
     */
    private fun sendCommandCompleted(commandId: String, result: Map<String, Any>) {
        val event = AudioChatEvent(
            event_name = "command.completed",
            user_id = deviceManager.userId,
            producer_id = deviceManager.deviceId,
            command_id = commandId,
            payload = mapOf(
                "command_id" to commandId,
                "result" to result
            )
        )
        deviceManager.sendEvent(event)
    }

    /**
     * 发送命令失败
     */
    private fun sendCommandFailed(commandId: String, message: String) {
        val event = AudioChatEvent(
            event_name = "command.failed",
            user_id = deviceManager.userId,
            producer_id = deviceManager.deviceId,
            command_id = commandId,
            payload = mapOf(
                "command_id" to commandId,
                "message" to message
            )
        )
        deviceManager.sendEvent(event)
    }

    /**
     * 获取本地 IP 地址
     */
    private fun getLocalIpAddress(): String {
        val wifiManager = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
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

    val running: Boolean
        get() = isRunning.get()

    val currentTaskInfo: TaskState?
        get() = currentTask
}
