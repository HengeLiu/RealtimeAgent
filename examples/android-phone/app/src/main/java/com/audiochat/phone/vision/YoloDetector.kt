package com.audiochat.phone.vision

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.util.Log

/**
 * YOLO 物体检测器
 * 使用 NCNN 推理引擎
 */
class YoloDetector(private val context: Context) {
    companion object {
        private const val TAG = "YoloDetector"

        private val COCO_CLASSES = arrayOf(
            "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
            "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
            "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
            "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
            "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
            "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
            "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake",
            "chair", "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
            "mouse", "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
            "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
            "toothbrush"
        )

        // 红绿灯模型类别
        private val TRAFFICLIGHT_CLASSES = arrayOf(
            "blank", "countdown_blank", "countdown_go", "countdown_stop",
            "crossing", "go", "stop"
        )

        private val CHINESE_CLASSES = mapOf(
            "person" to "人",
            "bicycle" to "自行车",
            "car" to "汽车",
            "motorcycle" to "摩托车",
            "bus" to "公交车",
            "train" to "火车",
            "truck" to "卡车",
            "traffic light" to "红绿灯",
            "bottle" to "瓶子",
            "cup" to "杯子",
            "bowl" to "碗",
            "chair" to "椅子",
            "couch" to "沙发",
            "cell phone" to "手机",
            "laptop" to "笔记本电脑",
            "book" to "书",
            "clock" to "时钟"
        )

        // 红绿灯模型中文类别
        private val TRAFFICLIGHT_CHINESE = mapOf(
            "blank" to "空白",
            "countdown_blank" to "倒计时-停",
            "countdown_go" to "倒计时-行",
            "countdown_stop" to "倒计时-停",
            "crossing" to "人行道",
            "go" to "通行",
            "stop" to "停止"
        )

        private fun classNameToId(className: String): Int {
            return COCO_CLASSES.indexOf(className).takeIf { it >= 0 } ?: 0
        }

        private fun classNameToChinese(className: String): String {
            return CHINESE_CLASSES[className] ?: className
        }

        private fun trafficlightClassToChinese(label: Int): String {
            return TRAFFICLIGHT_CLASSES.getOrNull(label)?.let { TRAFFICLIGHT_CHINESE[it] } ?: "未知"
        }
    }

    private var currentModelName: String = "yolov8n"

    data class Detection(
        val classId: Int,
        val className: String,
        val chineseName: String,
        val confidence: Float,
        val boundingBox: RectF
    )

    data class DetectionResult(
        val detections: List<Detection>,
        val annotatedBitmap: Bitmap,
        val inferenceTimeMs: Long
    )

    private val yoloNcnn = YoloNcnn()
    private var isModelLoaded = false
    private var inputSize = 320

    private val boxPaint = Paint().apply {
        color = Color.GREEN
        style = Paint.Style.STROKE
        strokeWidth = 8f
    }

    private val textPaint = Paint().apply {
        color = Color.WHITE
        textSize = 48f
        isAntiAlias = true
        typeface = android.graphics.Typeface.DEFAULT_BOLD
    }

    private val backgroundPaint = Paint().apply {
        color = Color.parseColor("#CC008800")
        style = Paint.Style.FILL
    }

    /**
     * 加载 NCNN 模型
     * @param modelName 模型名称 (支持 yolov8n, yolov8s, yolov10n 等)
     * @param inputSize 输入尺寸 (自动从模型配置获取，可选)
     */
    fun loadModel(modelName: String = "yolov8n", inputSize: Int? = null): Boolean {
        val modelInfo = VisionModels.findByName(modelName) ?: VisionModels.DEFAULT_MODEL
        this.inputSize = inputSize ?: modelInfo.inputSize
        this.currentModelName = modelName

        return try {
            val modelId = VisionModels.nameToModelId(modelName)
            val success = yoloNcnn.loadModel(context.assets, modelId)
            isModelLoaded = success
            if (success) {
                Log.i(TAG, "NCNN Model loaded: ${modelInfo.displayName} (id=$modelId, inputSize=${this.inputSize})")
            } else {
                Log.e(TAG, "Failed to load NCNN model: ${modelInfo.displayName}")
            }
            success
        } catch (e: Exception) {
            Log.e(TAG, "Failed to load model: ${modelInfo.displayName}", e)
            isModelLoaded = false
            false
        }
    }

    /**
     * 检测物体
     */
    fun detect(bitmap: Bitmap, confidenceThreshold: Float = 0.5f): DetectionResult {
        val startTime = System.currentTimeMillis()

        if (!isModelLoaded) {
            return DetectionResult(
                detections = emptyList(),
                annotatedBitmap = bitmap,
                inferenceTimeMs = 0
            )
        }

        try {
            val ncnnResults = yoloNcnn.detectPicure(bitmap)

            val detections = ncnnResults
                .filter { it.prob >= confidenceThreshold }
                .map { obj ->
                    val isTrafficLight = currentModelName.equals("trafficlight", ignoreCase = true)
                    val labelIndex = if (isTrafficLight) TRAFFICLIGHT_CLASSES.indexOf(obj.label).takeIf { it >= 0 } ?: 0 else classNameToId(obj.label)
                    Detection(
                        classId = labelIndex,
                        className = obj.label,
                        chineseName = if (isTrafficLight) trafficlightClassToChinese(labelIndex) else classNameToChinese(obj.label),
                        confidence = obj.prob,
                        boundingBox = RectF(obj.x, obj.y, obj.x + obj.w, obj.y + obj.h)
                    )
                }

            val annotatedBitmap = drawDetections(bitmap, detections)
            val inferenceTime = System.currentTimeMillis() - startTime

            return DetectionResult(
                detections = detections,
                annotatedBitmap = annotatedBitmap,
                inferenceTimeMs = inferenceTime
            )

        } catch (e: Exception) {
            Log.e(TAG, "Detection failed", e)
            return DetectionResult(
                detections = emptyList(),
                annotatedBitmap = bitmap,
                inferenceTimeMs = 0
            )
        }
    }

    /**
     * 查找特定物体
     */
    fun findObject(bitmap: Bitmap, objectName: String, confidenceThreshold: Float = 0.3f): Detection? {
        val result = detect(bitmap, confidenceThreshold)

        return result.detections.firstOrNull { det ->
            det.className.equals(objectName, ignoreCase = true) ||
            det.chineseName.contains(objectName, ignoreCase = true)
        }
    }

    /**
     * 检测红绿灯状态
     */
    fun detectTrafficLight(bitmap: Bitmap): TrafficLightState? {
        val result = detect(bitmap, 0.3f)

        val trafficLight = result.detections.firstOrNull {
            it.className == "traffic light"
        } ?: return null

        val box = trafficLight.boundingBox
        val trafficLightBitmap = Bitmap.createBitmap(
            bitmap,
            box.left.toInt().coerceAtLeast(0),
            box.top.toInt().coerceAtLeast(0),
            (box.width()).toInt().coerceAtMost(bitmap.width - box.left.toInt()),
            (box.height()).toInt().coerceAtMost(bitmap.height - box.top.toInt())
        )

        return analyzeTrafficLightColor(trafficLightBitmap)
    }

    /**
     * 分析红绿灯颜色
     */
    private fun analyzeTrafficLightColor(bitmap: Bitmap): TrafficLightState {
        var redCount = 0
        var greenCount = 0
        var yellowCount = 0

        for (y in 0 until bitmap.height step 4) {
            for (x in 0 until bitmap.width step 4) {
                val pixel = bitmap.getPixel(x, y)
                val r = Color.red(pixel)
                val g = Color.green(pixel)
                val b = Color.blue(pixel)

                when {
                    r > 150 && g < 100 && b < 100 -> redCount++
                    g > 150 && r < 100 && b < 100 -> greenCount++
                    r > 150 && g > 150 && b < 100 -> yellowCount++
                }
            }
        }

        return when {
            redCount > greenCount && redCount > yellowCount -> TrafficLightState.RED
            greenCount > redCount && greenCount > yellowCount -> TrafficLightState.GREEN
            yellowCount > redCount && yellowCount > greenCount -> TrafficLightState.YELLOW
            else -> TrafficLightState.UNKNOWN
        }
    }

    /**
     * 绘制检测结果
     */
    private fun drawDetections(bitmap: Bitmap, detections: List<Detection>): Bitmap {
        val result = bitmap.copy(Bitmap.Config.ARGB_8888, true)
        val canvas = Canvas(result)

        for (det in detections) {
            canvas.drawRect(det.boundingBox, boxPaint)

            val label = "${det.chineseName} ${(det.confidence * 100).toInt()}%"
            val textBounds = android.graphics.Rect()
            textPaint.getTextBounds(label, 0, label.length, textBounds)

            val textBgRect = RectF(
                det.boundingBox.left,
                det.boundingBox.top - textBounds.height() - 12,
                det.boundingBox.left + textBounds.width() + 20,
                det.boundingBox.top
            )
            canvas.drawRect(textBgRect, backgroundPaint)
            canvas.drawText(label, det.boundingBox.left + 10, det.boundingBox.top - 10, textPaint)
        }

        return result
    }

    /**
     * 释放资源
     */
    fun close() {
        isModelLoaded = false
    }

    val isReady: Boolean
        get() = isModelLoaded
}

enum class TrafficLightState {
    RED, GREEN, YELLOW, UNKNOWN;

    val chineseName: String
        get() = when (this) {
            RED -> "红灯"
            GREEN -> "绿灯"
            YELLOW -> "黄灯"
            UNKNOWN -> "未知"
        }

    val canCross: Boolean
        get() = this == GREEN
}