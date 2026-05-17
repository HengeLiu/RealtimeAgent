package com.audiochat.phone.vision

/**
 * 视觉模型配置
 * 支持多种模型 (YOLO, NanoDet 等)
 */
object VisionModels {

    /**
     * 模型信息
     */
    data class ModelInfo(
        val id: Int,
        val name: String,
        val displayName: String,
        val inputSize: Int,
        val description: String
    )

    // NCNN 模型列表 (与 JNI 层 modelId 对应)
    val NCNN_MODELS = listOf(
        ModelInfo(0, "yolov8n", "YOLOv8 Nano", 320, "轻量级 YOLOv8"),
        ModelInfo(1, "yolov8s", "YOLOv8 Small", 320, "小规模 YOLOv8"),
        ModelInfo(2, "yolov10n", "YOLOv10 Nano", 640, "轻量级 YOLOv10"),
        ModelInfo(3, "yolov10s", "YOLOv10 Small", 640, "小规模 YOLOv10"),
        ModelInfo(4, "yolov7-tiny", "YOLOv7 Tiny", 640, "轻量级 YOLOv7"),
        ModelInfo(5, "yolov5s", "YOLOv5 Small", 640, "小规模 YOLOv5"),
        ModelInfo(6, "rtmdet-nano", "RTMDet Nano", 320, "实时检测模型"),
        ModelInfo(7, "yolov11n", "YOLOv11 Nano", 640, "最新轻量级 YOLO"),
        ModelInfo(8, "trafficlight", "红绿灯检测", 640, "红绿灯状态检测")
    )

    // 红绿灯模型类别
    val TRAFFICLIGHT_CLASSES = mapOf(
        0 to "空白",
        1 to "倒计时-停",
        2 to "倒计时-行",
        3 to "倒计时-停",
        4 to "人行道",
        5 to "通行",
        6 to "停止"
    )

    // 默认模型
    val DEFAULT_MODEL = NCNN_MODELS.first { it.name == "yolov8n" }

    // 根据模型名查找模型信息
    fun findByName(name: String): ModelInfo? {
        val lowerName = name.lowercase()
        return NCNN_MODELS.find {
            it.name.equals(lowerName, ignoreCase = true) ||
            it.displayName.lowercase().contains(lowerName)
        }
    }

    // 根据模型名获取 NCNN modelId
    fun nameToModelId(name: String): Int {
        return findByName(name)?.id ?: DEFAULT_MODEL.id
    }

    // 根据模型名获取输入尺寸
    fun nameToInputSize(name: String): Int {
        return findByName(name)?.inputSize ?: DEFAULT_MODEL.inputSize
    }
}

/**
 * 当前使用的视觉检测器类型
 */
enum class DetectorType {
    YOLO_NCNN,    // NCNN YOLO
    YOLO_TFLITE,  // TensorFlow Lite YOLO (已弃用)
    NANODET       // NanoDet (预留)
}