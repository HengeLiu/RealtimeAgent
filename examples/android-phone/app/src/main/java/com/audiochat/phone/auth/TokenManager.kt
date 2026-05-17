package com.audiochat.phone.auth

import android.content.Context
import android.content.SharedPreferences
import android.util.Log

/**
 * Token 管理器
 * 
 * 主要功能：
 * - 保存和读取 Access Token 和 Refresh Token
 * - Token 自动刷新
 * - Token 过期检查
 */
class TokenManager(context: Context) {
    
    private val prefs: SharedPreferences = context.getSharedPreferences("auth_tokens", Context.MODE_PRIVATE)
    
    companion object {
        private const val TAG = "TokenManager"
        private const val KEY_ACCESS_TOKEN = "access_token"
        private const val KEY_REFRESH_TOKEN = "refresh_token"
        private const val KEY_EXPIRES_IN = "expires_in"
        private const val KEY_USER_ID = "user_id"
        private const val KEY_PHONE_NUMBER = "phone_number"
        private const val KEY_TOKEN_TYPE = "token_type"
        private const val KEY_SAVED_TIME = "saved_time"
    }
    
    /**
     * 保存认证信息
     */
    fun saveAuthInfo(
        accessToken: String,
        refreshToken: String,
        expiresIn: Int,
        userId: String,
        phoneNumber: String?,
        tokenType: String = "bearer"
    ) {
        prefs.edit()
            .putString(KEY_ACCESS_TOKEN, accessToken)
            .putString(KEY_REFRESH_TOKEN, refreshToken)
            .putInt(KEY_EXPIRES_IN, expiresIn)
            .putString(KEY_USER_ID, userId)
            .putString(KEY_PHONE_NUMBER, phoneNumber)
            .putString(KEY_TOKEN_TYPE, tokenType)
            .putLong(KEY_SAVED_TIME, System.currentTimeMillis())
            .apply()
        
        Log.i(TAG, "保存认证信息: userId=$userId, expiresIn=$expiresIn")
    }
    
    /**
     * 获取 Access Token
     */
    fun getAccessToken(): String? {
        return prefs.getString(KEY_ACCESS_TOKEN, null)
    }
    
    /**
     * 获取 Refresh Token
     */
    fun getRefreshToken(): String? {
        return prefs.getString(KEY_REFRESH_TOKEN, null)
    }
    
    /**
     * 获取用户ID
     */
    fun getUserId(): String? {
        return prefs.getString(KEY_USER_ID, null)
    }
    
    /**
     * 获取手机号
     */
    fun getPhoneNumber(): String? {
        return prefs.getString(KEY_PHONE_NUMBER, null)
    }
    
    /**
     * 检查 Access Token 是否有效
     */
    fun isAccessTokenValid(): Boolean {
        val accessToken = getAccessToken() ?: return false
        val savedTime = prefs.getLong(KEY_SAVED_TIME, 0)
        val expiresIn = prefs.getInt(KEY_EXPIRES_IN, 0)
        
        if (savedTime == 0L || expiresIn == 0) {
            return false
        }
        
        val expiresAt = savedTime + (expiresIn * 1000L)
        val now = System.currentTimeMillis()
        
        // 提前5分钟认为过期，避免临界情况
        val isValid = now < (expiresAt - 5 * 60 * 1000)
        
        Log.d(TAG, "Token有效性检查: isValid=$isValid, expiresAt=${expiresAt - now}ms后过期")
        
        return isValid
    }
    
    /**
     * 检查是否已登录
     */
    fun isLoggedIn(): Boolean {
        return getAccessToken() != null && getRefreshToken() != null
    }
    
    /**
     * 清除认证信息
     */
    fun clearAuthInfo() {
        prefs.edit()
            .remove(KEY_ACCESS_TOKEN)
            .remove(KEY_REFRESH_TOKEN)
            .remove(KEY_EXPIRES_IN)
            .remove(KEY_USER_ID)
            .remove(KEY_PHONE_NUMBER)
            .remove(KEY_TOKEN_TYPE)
            .remove(KEY_SAVED_TIME)
            .apply()
        
        Log.i(TAG, "清除认证信息")
    }
    
    /**
     * 获取完整的认证信息
     */
    fun getAuthInfo(): AuthInfo? {
        val accessToken = getAccessToken() ?: return null
        val refreshToken = getRefreshToken() ?: return null
        val userId = getUserId() ?: return null
        
        return AuthInfo(
            accessToken = accessToken,
            refreshToken = refreshToken,
            expiresIn = prefs.getInt(KEY_EXPIRES_IN, 0),
            userId = userId,
            phoneNumber = getPhoneNumber(),
            tokenType = prefs.getString(KEY_TOKEN_TYPE, "bearer") ?: "bearer"
        )
    }
    
    /**
     * 认证信息数据类
     */
    data class AuthInfo(
        val accessToken: String,
        val refreshToken: String,
        val expiresIn: Int,
        val userId: String,
        val phoneNumber: String?,
        val tokenType: String
    )
}
