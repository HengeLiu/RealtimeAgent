package com.audiochat.phone.error

import android.content.Context
import android.widget.Toast
import android.os.Handler
import android.os.Looper

object ErrorToastManager {
    private val mainHandler = Handler(Looper.getMainLooper())
    private var lastToastTime = 0L
    private const val MIN_INTERVAL = 2000L

    fun showError(context: Context, message: String, duration: Int = Toast.LENGTH_LONG) {
        val currentTime = System.currentTimeMillis()
        
        if (currentTime - lastToastTime < MIN_INTERVAL) {
            return
        }
        
        lastToastTime = currentTime
        
        mainHandler.post {
            Toast.makeText(context, message, duration).show()
        }
    }

    fun showSuccess(context: Context, message: String) {
        val currentTime = System.currentTimeMillis()
        
        if (currentTime - lastToastTime < MIN_INTERVAL) {
            return
        }
        
        lastToastTime = currentTime
        
        mainHandler.post {
            Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
        }
    }

    fun showInfo(context: Context, message: String) {
        val currentTime = System.currentTimeMillis()
        
        if (currentTime - lastToastTime < MIN_INTERVAL) {
            return
        }
        
        lastToastTime = currentTime
        
        mainHandler.post {
            Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
        }
    }

    fun showWarning(context: Context, message: String) {
        showError(context, message, Toast.LENGTH_LONG)
    }

    fun showAppError(context: Context, error: AppError) {
        if (error.shouldShowToUser) {
            showError(context, error.userMessage)
        }
    }
}
