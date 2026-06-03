package com.audiochat.phone.protocol

import com.google.gson.Gson
import com.google.gson.GsonBuilder
import com.google.gson.reflect.TypeToken
import java.lang.reflect.Type

/**
 * Gson 工厂
 */
object GsonFactory {
    val gson: Gson = GsonBuilder()
        .setPrettyPrinting()
        .serializeNulls()
        .create()

    inline fun <reified T> fromJson(json: String): T {
        return gson.fromJson(json, object : TypeToken<T>() {}.type)
    }

    fun <T> fromJson(json: String, type: Type): T {
        return gson.fromJson(json, type)
    }
}