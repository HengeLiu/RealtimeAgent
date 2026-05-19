plugins {
    kotlin("jvm")
    `java-library`
}

dependencies {
    // JSON 序列化
    implementation("com.google.code.gson:gson:2.10.1")

    // WebSocket (OkHttp)
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // Kotlin 协程
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")

    // 测试
    testImplementation(kotlin("test"))
}

tasks.test {
    useJUnitPlatform()
}

kotlin {
    jvmToolchain(11)
}