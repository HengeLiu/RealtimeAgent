# Add project specific ProGuard rules here.
-keepattributes *Annotation*
-keep class com.realtimeagent.device.protocol.** { *; }
-keep class com.realtimeagent.device.device.** { *; }
-dontwarn okio.**
-dontwarn okhttp3.**

# 阿里云号码认证 SDK 混淆规则
-keepattributes Exceptions,InnerClasses,Signature,Deprecated,*Annotation*,EnclosingMethod
-keep class android.app.ActivityThread {*;}
-keep class android.os.SystemProperties {*;}
-keep class com.mobile.auth.** {*;}
-keep class com.cmic.** {*;}
-keep class com.unicom.** {*;}
-keep class com.ct.** {*;}