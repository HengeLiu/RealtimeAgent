package com.audiochat.phone.auth

import android.app.Activity
import android.content.Context
import android.util.Log
import com.mobile.auth.gatewayauth.AuthUIConfig
import com.mobile.auth.gatewayauth.PhoneNumberAuthHelper
import com.mobile.auth.gatewayauth.ResultCode
import com.mobile.auth.gatewayauth.TokenResultListener
import com.mobile.auth.gatewayauth.model.TokenRet

/**
 * 阿里云号码认证管理器
 * 
 * 完整实现阿里云号码认证 SDK 一键登录功能
 * 文档: https://help.aliyun.com/zh/pnvs/developer-reference/the-android-client-access
 */
class AliyunAuthManager(private val context: Context) {
    companion object {
        private const val TAG = "AliyunAuthManager"
    }

    private var mPhoneNumberAuthHelper: PhoneNumberAuthHelper? = null
    private var mTokenResultListener: TokenResultListener? = null
    private var mAuthCallback: ((success: Boolean, token: String?, message: String) -> Unit)? = null

    /**
     * 初始化 SDK
     * 
     * @param secretInfo 阿里云控制台申请的认证方案密钥
     */
    fun init(secretInfo: String) {
        mTokenResultListener = object : TokenResultListener {
            override fun onTokenSuccess(s: String?) {
                Log.i(TAG, ">>> onTokenSuccess: $s")
                
                try {
                    val tokenRet = TokenRet.fromJson(s)
                    if (tokenRet != null) {
                        Log.i(TAG, "Token result code: ${tokenRet.code}, msg: ${tokenRet.msg}")
                        
                        when (tokenRet.code) {
                            ResultCode.CODE_START_AUTHPAGE_SUCCESS -> {
                                Log.i(TAG, "唤起授权页成功")
                            }
                            
                            ResultCode.CODE_SUCCESS -> {
                                Log.i(TAG, "获取token成功")
                                val token = tokenRet.token
                                if (token != null && token.isNotEmpty()) {
                                    Log.i(TAG, "Token length: ${token.length}")
                                    mAuthCallback?.invoke(true, token, "认证成功")
                                    mAuthCallback = null
                                    mPhoneNumberAuthHelper?.setAuthListener(null)
                                } else {
                                    Log.e(TAG, "Token is null or empty")
                                    mAuthCallback?.invoke(false, null, "获取Token失败")
                                    mAuthCallback = null
                                }
                            }
                            
                            else -> {
                                Log.w(TAG, "Token success with other code: ${tokenRet.code}, msg: ${tokenRet.msg}")
                                mAuthCallback?.invoke(false, null, "认证失败: ${tokenRet.msg}")
                                mAuthCallback = null
                            }
                        }
                    } else {
                        Log.e(TAG, "TokenRet is null")
                        mAuthCallback?.invoke(false, null, "解析结果失败")
                        mAuthCallback = null
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Parse token result error", e)
                    mAuthCallback?.invoke(false, null, "解析结果失败: ${e.message}")
                    mAuthCallback = null
                }
            }

            override fun onTokenFailed(s: String?) {
                Log.e(TAG, ">>> onTokenFailed: $s")
                
                try {
                    val tokenRet = TokenRet.fromJson(s)
                    val message = tokenRet?.msg ?: "认证失败"
                    val code = tokenRet?.code ?: -1
                    Log.e(TAG, "Token failed with code: $code, msg: $message")
                    mAuthCallback?.invoke(false, null, message)
                    mAuthCallback = null
                    mPhoneNumberAuthHelper?.hideLoginLoading()
                } catch (e: Exception) {
                    Log.e(TAG, "Parse token error error", e)
                    mAuthCallback?.invoke(false, null, "认证失败: ${e.message}")
                    mAuthCallback = null
                }
            }
        }

        mPhoneNumberAuthHelper = PhoneNumberAuthHelper.getInstance(context, mTokenResultListener)
        mPhoneNumberAuthHelper?.reporter?.setLoggerEnable(true)
        mPhoneNumberAuthHelper?.setAuthSDKInfo(secretInfo)
        
        Log.i(TAG, "Aliyun Auth SDK initialized successfully")
    }

    /**
     * 检查环境是否支持一键登录
     */
    fun checkEnvAvailable() {
        mPhoneNumberAuthHelper?.checkEnvAvailable()
        Log.d(TAG, "Check env available")
    }

    /**
     * 配置授权页 UI
     */
    fun configAuthPage() {
        val authUIConfig = AuthUIConfig.Builder()
            .setNavColor(0xFF1E88E5.toInt())
            .setNavText("一键登录")
            .setNavTextColor(0xFFFFFFFF.toInt())
            .setNumberColor(0xFF333333.toInt())
            .setSloganText("欢迎登录")
            .setLogBtnText("本机号码一键登录")
            .setLogBtnTextColor(0xFFFFFFFF.toInt())
            .setSwitchAccHidden(true)
            .setAppPrivacyOne("用户协议", "https://example.com/user-agreement")
            .setAppPrivacyTwo("隐私政策", "https://example.com/privacy-policy")
            .setPrivacyState(true)
            .create()
        
        mPhoneNumberAuthHelper?.setAuthUIConfig(authUIConfig)
    }

    /**
     * 用户控制返回键及左上角返回按钮效果
     */
    fun userControlAuthPageCancel() {
        mPhoneNumberAuthHelper?.userControlAuthPageCancel()
    }

    /**
     * 授权页是否跟随系统深色模式
     */
    fun setAuthPageUseDayLight(useDayLight: Boolean) {
        mPhoneNumberAuthHelper?.setAuthPageUseDayLight(useDayLight)
    }

    /**
     * 横屏水滴屏全屏适配
     */
    fun keepAuthPageLandscapeFullScreen(keep: Boolean) {
        mPhoneNumberAuthHelper?.keepAuthPageLandscapeFullSreen(keep)
    }

    /**
     * SDK 内置所有界面隐藏底部导航栏
     */
    fun keepAllPageHideNavigationBar() {
        mPhoneNumberAuthHelper?.keepAllPageHideNavigationBar()
    }

    /**
     * 授权页扩大协议按钮选择范围至我已阅读并同意
     */
    fun expandAuthPageCheckedScope(expand: Boolean) {
        mPhoneNumberAuthHelper?.expandAuthPageCheckedScope(expand)
    }

    /**
     * 一键登录
     * 
     * @param activity 当前 Activity
     * @param timeout 超时时间（毫秒）
     * @param callback 回调函数，返回是否成功、token 和消息
     */
    fun startOneClickAuth(
        activity: Activity,
        timeout: Int = 5000,
        callback: (success: Boolean, token: String?, message: String) -> Unit
    ) {
        mAuthCallback = callback

        try {
            checkEnvAvailable()
            configAuthPage()
            userControlAuthPageCancel()
            setAuthPageUseDayLight(true)
            keepAuthPageLandscapeFullScreen(true)
            keepAllPageHideNavigationBar()
            expandAuthPageCheckedScope(true)
            
            mPhoneNumberAuthHelper?.setAuthListener(mTokenResultListener)
            mPhoneNumberAuthHelper?.getLoginToken(activity, timeout)
            Log.i(TAG, "Start one-click auth with timeout: $timeout ms")
        } catch (e: Exception) {
            Log.e(TAG, "One-click auth error", e)
            mAuthCallback?.invoke(false, null, "认证异常: ${e.message}")
            mAuthCallback = null
        }
    }

    /**
     * 退出登录页面
     */
    fun quitLoginPage() {
        try {
            mPhoneNumberAuthHelper?.quitLoginPage()
            Log.d(TAG, "Quit login page")
        } catch (e: Exception) {
            Log.e(TAG, "Quit login page failed", e)
        }
    }

    /**
     * 隐藏登录加载动画
     */
    fun hideLoginLoading() {
        try {
            mPhoneNumberAuthHelper?.hideLoginLoading()
            Log.d(TAG, "Hide login loading")
        } catch (e: Exception) {
            Log.e(TAG, "Hide login loading failed", e)
        }
    }

    /**
     * 释放资源
     */
    fun release() {
        try {
            quitLoginPage()
            mPhoneNumberAuthHelper?.setAuthListener(null)
            mPhoneNumberAuthHelper = null
            mTokenResultListener = null
            mAuthCallback = null
            Log.d(TAG, "Released")
        } catch (e: Exception) {
            Log.e(TAG, "Release failed", e)
        }
    }
}
