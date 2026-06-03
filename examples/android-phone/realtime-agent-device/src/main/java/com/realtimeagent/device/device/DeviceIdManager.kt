package com.realtimeagent.device.device

import android.content.Context
import android.provider.Settings
import java.util.UUID

object DeviceIdManager {
    private const val PREFS_NAME = "device_identity"
    private const val PREF_DEVICE_ID = "device_id"
    private const val PREF_DEVICE_SALT = "device_salt"

    fun getDeviceId(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        val existingId = prefs.getString(PREF_DEVICE_ID, null)
        if (existingId != null) {
            return existingId
        }

        val androidId = getAndroidId(context)
        val newId = if (androidId != null) {
            "phone-$androidId"
        } else {
            val salt = UUID.randomUUID().toString()
            prefs.edit().putString(PREF_DEVICE_SALT, salt).apply()
            val uuid = UUID.nameUUIDFromBytes(salt.toByteArray())
            "phone-$uuid"
        }

        prefs.edit().putString(PREF_DEVICE_ID, newId).apply()
        return newId
    }

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

    fun resetDeviceId(context: Context): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        prefs.edit().clear().apply()
        return getDeviceId(context)
    }
}
