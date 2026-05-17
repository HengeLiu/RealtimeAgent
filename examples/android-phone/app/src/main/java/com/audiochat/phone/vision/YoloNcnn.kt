package com.audiochat.phone.vision

import android.content.res.AssetManager
import android.graphics.Bitmap
import android.view.Surface

/**
 * NCNN YOLO 检测器 JNI 封装
 * 复刻 com.tencent.yoloncnn.YoloNcnn
 */
class YoloNcnn {

    data class Obj(
        var x: Float = 0f,
        var y: Float = 0f,
        var w: Float = 0f,
        var h: Float = 0f,
        var label: String = "",
        var prob: Float = 0f
    )

    // 模型ID: 0=v8n, 1=v8s, 2=v10n, 3=v10s, 4=v7-tiny, 5=v5s, 6=rtmdet-nano, 7=v11n
    companion object {
        const val MODEL_V8N = 0
        const val MODEL_V8S = 1
        const val MODEL_V10N = 2
        const val MODEL_V10S = 3
        const val MODEL_V7_TINY = 4
        const val MODEL_V5S = 5
        const val MODEL_RTMNET_NANO = 6
        const val MODEL_V11N = 7

        const val CPU = 0
        const val GPU = 1

        init {
            System.loadLibrary("yoloncnn")
        }
    }

    external fun loadModel(mgr: AssetManager, modelid: Int, cpugpu: Int): Boolean
    external fun openCamera(facing: Int): Boolean
    external fun closeCamera(): Boolean
    external fun setOutputWindow(surface: Surface): Boolean
    external fun detect(start: Boolean)
    external fun initGlobalObj()
    external fun getDetect(): Boolean
    external fun callNativeMethod()
    external fun detectPicure(bitmap: Bitmap): Array<Obj>

    fun loadModel(mgr: AssetManager, modelId: Int): Boolean {
        return loadModel(mgr, modelId, CPU)
    }

    fun detect(bitmap: Bitmap): Array<Obj> {
        return detectPicure(bitmap)
    }
}