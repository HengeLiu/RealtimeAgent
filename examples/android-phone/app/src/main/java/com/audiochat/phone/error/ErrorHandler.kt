package com.audiochat.phone.error

import android.util.Log

enum class ErrorSeverity {
    INFO,
    WARNING,
    ERROR,
    CRITICAL
}

data class AppError(
    val code: String,
    val userMessage: String,
    val internalMessage: String,
    val severity: ErrorSeverity,
    val shouldShowToUser: Boolean = true
)

object ErrorHandler {
    private const val TAG = "ErrorHandler"

    fun handleAuthError(errorCode: String, originalMessage: String): AppError {
        Log.e(TAG, "Auth error: code=$errorCode, message=$originalMessage")
        
        return when (errorCode) {
            "INVALID_PHONE" -> AppError(
                code = errorCode,
                userMessage = "请输入正确的手机号",
                internalMessage = "手机号格式错误: $originalMessage",
                severity = ErrorSeverity.WARNING,
                shouldShowToUser = true
            )
            
            "NETWORK_UNAVAILABLE" -> AppError(
                code = errorCode,
                userMessage = "网络不可用，请检查网络连接",
                internalMessage = "网络不可用",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            "APPID_SECRET_ERROR" -> AppError(
                code = errorCode,
                userMessage = "应用配置错误",
                internalMessage = "AppID Secret解析失败: $originalMessage",
                severity = ErrorSeverity.CRITICAL,
                shouldShowToUser = true
            )
            
            "AUTH_PAGE_FAILED" -> AppError(
                code = errorCode,
                userMessage = "无法打开登录页面，请重试",
                internalMessage = "唤起授权页失败: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            "TOKEN_FAILED" -> AppError(
                code = errorCode,
                userMessage = "获取授权失败，请重试",
                internalMessage = "获取Token失败: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            "CARRIER_FAILED" -> AppError(
                code = errorCode,
                userMessage = "运营商服务暂时不可用",
                internalMessage = "运营商服务失败: $originalMessage",
                severity = ErrorSeverity.WARNING,
                shouldShowToUser = true
            )
            
            "VENDOR_FAILED" -> AppError(
                code = errorCode,
                userMessage = "服务暂时不可用，请稍后重试",
                internalMessage = "服务提供商失败: $originalMessage",
                severity = ErrorSeverity.WARNING,
                shouldShowToUser = true
            )
            
            "NETWORK_FAILED" -> AppError(
                code = errorCode,
                userMessage = "网络连接失败，请检查网络",
                internalMessage = "网络连接失败: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            "TIMEOUT" -> AppError(
                code = errorCode,
                userMessage = "请求超时，请重试",
                internalMessage = "请求超时: $originalMessage",
                severity = ErrorSeverity.WARNING,
                shouldShowToUser = true
            )
            
            "REQUEST_FAILED" -> AppError(
                code = errorCode,
                userMessage = "请求失败，请重试",
                internalMessage = "请求失败: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            "OUT_OF_SERVICE", "CREDIT_CONTROLLED" -> AppError(
                code = errorCode,
                userMessage = "服务暂时不可用，请稍后重试",
                internalMessage = "阿里云服务额度不足: $originalMessage",
                severity = ErrorSeverity.CRITICAL,
                shouldShowToUser = false
            )
            
            "RATE_LIMIT" -> AppError(
                code = errorCode,
                userMessage = "操作过于频繁，请稍后重试",
                internalMessage = "请求频率限制: $originalMessage",
                severity = ErrorSeverity.WARNING,
                shouldShowToUser = true
            )
            
            "PARSE_ERROR" -> AppError(
                code = errorCode,
                userMessage = "数据处理失败，请重试",
                internalMessage = "数据解析失败: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            "INVALID_ACCESS_CODE" -> AppError(
                code = errorCode,
                userMessage = "授权失败，请重试",
                internalMessage = "授权码无效: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            "INVALID_TOKEN" -> AppError(
                code = errorCode,
                userMessage = "登录已过期，请重新登录",
                internalMessage = "Token无效: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            else -> AppError(
                code = errorCode,
                userMessage = "登录失败，请重试",
                internalMessage = "未知错误: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
        }
    }

    fun handleHttpError(statusCode: Int, originalMessage: String): AppError {
        Log.e(TAG, "HTTP error: status=$statusCode, message=$originalMessage")
        
        return when (statusCode) {
            400 -> AppError(
                code = "HTTP_400",
                userMessage = "请求参数错误",
                internalMessage = "HTTP 400: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            401 -> AppError(
                code = "HTTP_401",
                userMessage = "未授权，请重新登录",
                internalMessage = "HTTP 401: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            403 -> AppError(
                code = "HTTP_403",
                userMessage = "访问被拒绝",
                internalMessage = "HTTP 403: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            404 -> AppError(
                code = "HTTP_404",
                userMessage = "服务不存在",
                internalMessage = "HTTP 404: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            500 -> AppError(
                code = "HTTP_500",
                userMessage = "服务器错误，请稍后重试",
                internalMessage = "HTTP 500: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            502 -> AppError(
                code = "HTTP_502",
                userMessage = "服务暂时不可用",
                internalMessage = "HTTP 502: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            503 -> AppError(
                code = "HTTP_503",
                userMessage = "服务暂时不可用",
                internalMessage = "HTTP 503: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            else -> AppError(
                code = "HTTP_$statusCode",
                userMessage = "网络请求失败",
                internalMessage = "HTTP $statusCode: $originalMessage",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
        }
    }

    fun handleNetworkError(exception: Throwable): AppError {
        Log.e(TAG, "Network error", exception)
        
        val message = exception.message ?: "未知网络错误"
        
        return when {
            message.contains("timeout", ignoreCase = true) -> AppError(
                code = "TIMEOUT",
                userMessage = "请求超时，请检查网络",
                internalMessage = "网络超时: $message",
                severity = ErrorSeverity.WARNING,
                shouldShowToUser = true
            )
            
            message.contains("network", ignoreCase = true) -> AppError(
                code = "NETWORK_ERROR",
                userMessage = "网络连接失败",
                internalMessage = "网络错误: $message",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            else -> AppError(
                code = "NETWORK_ERROR",
                userMessage = "网络请求失败",
                internalMessage = "网络异常: $message",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
        }
    }

    fun handleServerError(message: String): AppError {
        Log.e(TAG, "Server error: $message")
        
        return when {
            message.contains("OUT_OF_SERVICE", ignoreCase = true) ||
            message.contains("credit controlled", ignoreCase = true) -> AppError(
                code = "OUT_OF_SERVICE",
                userMessage = "服务暂时不可用，请稍后重试",
                internalMessage = "阿里云服务额度不足: $message",
                severity = ErrorSeverity.CRITICAL,
                shouldShowToUser = false
            )
            
            message.contains("ACCESS_CODE_ILLEGAL", ignoreCase = true) -> AppError(
                code = "INVALID_ACCESS_CODE",
                userMessage = "授权失败，请重试",
                internalMessage = "授权码无效: $message",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            message.contains("TOKEN_INVALID", ignoreCase = true) -> AppError(
                code = "INVALID_TOKEN",
                userMessage = "登录已过期，请重新登录",
                internalMessage = "Token无效: $message",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
            
            message.contains("RATE_LIMIT", ignoreCase = true) -> AppError(
                code = "RATE_LIMIT",
                userMessage = "操作过于频繁，请稍后重试",
                internalMessage = "请求频率限制: $message",
                severity = ErrorSeverity.WARNING,
                shouldShowToUser = true
            )
            
            else -> AppError(
                code = "SERVER_ERROR",
                userMessage = "服务暂时不可用，请稍后重试",
                internalMessage = "服务器错误: $message",
                severity = ErrorSeverity.ERROR,
                shouldShowToUser = true
            )
        }
    }
}
