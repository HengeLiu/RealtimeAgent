package com.audiochat.phone.util

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 日期时间工具类
 */
object DateUtil {
    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.getDefault())
    private val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault())

    fun currentTime(): String = timeFormat.format(Date())

    fun currentDateTime(): String = dateFormat.format(Date())

    fun timestamp(): Long = System.currentTimeMillis()
}

/**
 * JSON 转换扩展
 */
object JsonUtil {
    @PublishedApi
    internal val gson = com.google.gson.GsonBuilder()
        .setPrettyPrinting()
        .serializeNulls()
        .create()

    fun toJson(obj: Any?): String = gson.toJson(obj)

    inline fun <reified T> fromJson(json: String): T {
        return gson.fromJson(json, object : com.google.gson.reflect.TypeToken<T>() {}.type)
    }
}

/**
 * ID 生成工具
 */
object IdUtil {
    fun newStreamId(prefix: String = "stream"): String = "${prefix}_${System.currentTimeMillis()}_${(0..9999).random()}"

    fun newRequestId(): String = "req_${System.currentTimeMillis()}_${(0..9999).random()}"
}