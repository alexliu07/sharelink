// ShareLink 安卓小工具：把别处分享过来的文件/文字 POST 给自建服务，拿回分享码。
// 刻意不引入任何第三方依赖（连 AndroidX 都不用），以便在 CI 里稳定构建、APK 也小。
plugins {
    id("com.android.application")
}

android {
    namespace = "me.skylare.sharelink"
    compileSdk = 34

    defaultConfig {
        applicationId = "me.skylare.sharelink"
        minSdk = 24
        targetSdk = 34
        versionCode = 12
        versionName = "1.11"
    }

    // 显式指向仓库里的 keystore。不要用 signingConfigs.getByName("debug")：
    // 那个配置的实际用法是「没有就自己生成一张」，于是每次 CI 都会换一把密钥，
    // 装新版本就会因为签名不一致而必须先卸载（踩过这个坑）。
    signingConfigs {
        create("release-key") {
            storeFile = rootProject.file("keystore/debug.keystore")
            storeType = "PKCS12"
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release-key")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
