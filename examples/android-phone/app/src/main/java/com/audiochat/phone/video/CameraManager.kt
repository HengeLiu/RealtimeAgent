package com.audiochat.phone.video

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import kotlinx.coroutines.suspendCancellableCoroutine
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * 摄像头管理器
 * 完全复刻 Python 端的 RGB 采集功能：
 * - 拍照 (single mode)
 * - JPEG 格式输出
 */
class CameraManager(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner? = null
) {
    companion object {
        private const val TAG = "CameraManager"
        private const val JPEG_QUALITY = 90
    }

    private var cameraProvider: ProcessCameraProvider? = null
    private var imageCapture: ImageCapture? = null
    private var preview: Preview? = null
    
    private val cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()

    /**
     * 初始化摄像头
     */
    suspend fun initialize(): Boolean {
        return try {
            val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
            cameraProvider = cameraProviderFuture.get()
            
            imageCapture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                .setFlashMode(ImageCapture.FLASH_MODE_AUTO)
                .build()
            
            Log.i(TAG, "摄像头初始化成功")
            true
        } catch (e: Exception) {
            Log.e(TAG, "摄像头初始化失败", e)
            false
        }
    }

    /**
     * 绑定到生命周期（用于预览）
     */
    fun bindToLifecycle(lifecycleOwner: LifecycleOwner, previewView: androidx.camera.view.PreviewView?) {
        if (cameraProvider == null || imageCapture == null) return

        try {
            // 取消所有绑定
            cameraProvider?.unbindAll()

            // 创建预览
            if (previewView != null) {
                preview = Preview.Builder().build()
                preview?.setSurfaceProvider(previewView.surfaceProvider)
            }

            // 绑定到生命周期
            val useCases = mutableListOf<androidx.camera.core.UseCase>(imageCapture!!)
            preview?.let { useCases.add(0, it) }

            cameraProvider?.bindToLifecycle(
                lifecycleOwner,
                CameraSelector.DEFAULT_BACK_CAMERA,
                *useCases.toTypedArray()
            )
            
            Log.i(TAG, "摄像头已绑定到生命周期")
        } catch (e: Exception) {
            Log.e(TAG, "绑定摄像头失败", e)
        }
    }

    /**
     * 拍照并返回 JPEG 数据
     */
    suspend fun capturePhoto(): ByteArray? {
        return suspendCancellableCoroutine { continuation ->
            if (imageCapture == null) {
                continuation.resumeWithException(IllegalStateException("摄像头未初始化"))
                return@suspendCancellableCoroutine
            }

            val outputFileOptions = ImageCapture.OutputFileOptions.Builder(
                java.io.File.createTempFile("photo_", ".jpg", context.cacheDir)
            ).build()

            imageCapture?.takePicture(
                outputFileOptions,
                cameraExecutor,
                object : ImageCapture.OnImageSavedCallback {
                    override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                        try {
                            val jpegData = output.savedUri?.let { uri ->
                                context.contentResolver.openInputStream(uri)?.use { input ->
                                    input.readBytes()
                                }
                            } ?: run {
                                Log.w(TAG, "无法读取拍照结果")
                                null
                            }

                            // 删除临时文件
                            output.savedUri?.path?.let { path ->
                                java.io.File(path).delete()
                            }

                            continuation.resume(jpegData)
                        } catch (e: Exception) {
                            Log.e(TAG, "处理拍照结果失败", e)
                            continuation.resumeWithException(e)
                        }
                    }

                    override fun onError(exception: ImageCaptureException) {
                        Log.e(TAG, "拍照失败", exception)
                        continuation.resumeWithException(exception)
                    }
                }
            )

            continuation.invokeOnCancellation {
                Log.d(TAG, "拍照已取消")
            }
        }
    }

    /**
     * 拍照并压缩到指定大小以下
     */
    suspend fun captureCompressedPhoto(maxSizeKB: Int = 180): ByteArray? {
        val originalJpeg = capturePhoto() ?: return null
        
        // 如果已经足够小，直接返回
        if (originalJpeg.size <= maxSizeKB * 1024) {
            return originalJpeg
        }

        // 压缩图片
        return compressJpeg(originalJpeg, maxSizeKB)
    }

    /**
     * 压缩 JPEG 图片
     */
    private fun compressJpeg(jpegData: ByteArray, targetSizeKB: Int): ByteArray? {
        return try {
            val bitmap = BitmapFactory.decodeByteArray(jpegData, 0, jpegData.size) ?: return null
            
            var quality = JPEG_QUALITY
            var result: ByteArray
            
            do {
                ByteArrayOutputStream().use { stream ->
                    bitmap.compress(Bitmap.CompressFormat.JPEG, quality, stream)
                    result = stream.toByteArray()
                }
                
                quality -= 10
                
                if (quality < 20) break
                
            } while (result.size > targetSizeKB * 1024)

            Log.d(TAG, "图片压缩完成: ${jpegData.size} -> ${result.size} bytes")
            result
        } catch (e: Exception) {
            Log.e(TAG, "压缩图片失败", e)
            jpegData // 返回原始数据
        }
    }

    /**
     * 旋转 Bitmap
     */
    private fun rotateBitmap(bitmap: Bitmap, degrees: Float): Bitmap {
        val matrix = Matrix()
        matrix.postRotate(degrees)
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
    }

    /**
     * 解绑摄像头
     */
    fun unbind() {
        try {
            cameraProvider?.unbindAll()
            Log.i(TAG, "摄像头已解绑")
        } catch (e: Exception) {
            Log.e(TAG, "解绑摄像头失败", e)
        }
    }

    /**
     * 释放资源
     */
    fun release() {
        unbind()
        
        try {
            cameraExecutor.shutdown()
            cameraProvider = null
            imageCapture = null
            preview = null
        } catch (e: Exception) {
            Log.e(TAG, "释放资源失败", e)
        }
    }

    val isInitialized: Boolean
        get() = cameraProvider != null && imageCapture != null
}