package com.audiochat.phone.device

import android.content.Context
import android.provider.Settings
import java.util.UUID

/**
 * 设备 ID 管理器
 * 生成并持久化唯一的设备标识符
 */
object DeviceIdManager {
    private const val PREFS_NAME = "device_identity"
    private const val PREF_DEVICE_ID = "device_id"
    private const val PREF_DEVICE_SALT = "device_salt"

    /**
     * 获取或生成唯一的设备 ID
     * 格式: phone-{ANDROID_ID} 或 phone-{UUID}
     */
    fun getDeviceId(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        // 尝试获取已保存的设备 ID
        val existingId = prefs.getString(PREF_DEVICE_ID, null)
        if (existingId != null) {
            return existingId
        }

        // 生成新的设备 ID
        val androidId = getAndroidId(context)
        val newId = if (androidId != null) {
            "phone-$androidId"
        } else {
            // 如果无法获取 Android ID，使用 UUID
            val salt = UUID.randomUUID().toString()
            prefs.edit().putString(PREF_DEVICE_SALT, salt).apply()
            val uuid = UUID.nameUUIDFromBytes(salt.toByteArray())
            "phone-$uuid"
        }

        // 保存并返回
        prefs.edit().putString(PREF_DEVICE_ID, newId).apply()
        return newId
    }

    /**
     * 获取 Android ID
     * 这是设备首次启动时系统生成的唯一标识符
     */
    private fun getAndroidId(context: Context): String? {
        return try {
            Settings.Secure.getString(
                context.contentResolver,
                Settings.Secure.ANDROID_ID
            )
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 重置设备 ID（用于调试或重置场景）
     */
    fun resetDeviceId(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().clear().apply()
        return getDeviceId(context)
    }
}