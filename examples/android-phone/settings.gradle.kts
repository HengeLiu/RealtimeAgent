pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_EXPOSED)
    repositories {
        google()
        mavenCentral()
        maven { url = uri("https://dl.google.com/dl/android/maven/") }
    }
}

rootProject.name = "AudioChatPhone"
include(":app")
include(":$rootDir/../../audio-device/kotlin/device")