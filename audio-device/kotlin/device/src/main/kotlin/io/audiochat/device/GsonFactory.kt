package io.audiochat.device

import com.google.gson.Gson
import com.google.gson.GsonBuilder

/**
 * JSON 序列化/反序列化工厂
 */
object GsonFactory {
    private val builder = GsonBuilder()
        .serializeNulls()
        .disableHtmlEscaping()

    val gson: Gson = builder.create()

    inline fun <reified T> fromJson(json: String): T {
        return gson.fromJson(json, T::class.java)
    }

    fun toJson(obj: Any): String = gson.toJson(obj)
}