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
        versionCode = 2
        versionName = "1.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // 用固定的调试签名（仓库里带了 keystore，CI 会拷到 ~/.android/debug.keystore），
            // 这样每次装新版本都是"升级"，不会因为签名变了而要求先卸载。
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
