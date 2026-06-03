package com.audiochat.phone

import android.app.Application
import timber.log.Timber

class AudioChatApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        
        Timber.plant(Timber.DebugTree())
        
        Timber.i("AudioChat Phone App 已启动")
    }
}