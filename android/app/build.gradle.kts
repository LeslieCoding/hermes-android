plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// CI passes these; local builds fall back to sane defaults.
val appVersionCode = (System.getenv("HERMES_ANDROID_VERSION_CODE") ?: "1").toInt()
val appVersionName = System.getenv("HERMES_ANDROID_VERSION_NAME") ?: "0.1.0-dev"

val keystoreFile = System.getenv("HERMES_ANDROID_KEYSTORE")?.let { file(it) }
val hasReleaseKey = keystoreFile?.exists() == true

android {
    namespace = "io.github.hermesandroid"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.hermesandroid"
        // The upstream runtime targets Android API 24 (android_24_arm64_v8a wheels).
        minSdk = 24
        // Deliberately 28, like Termux: from API 29 on, Android forbids executing
        // files from an app's writable data directory, which is exactly where the
        // bundled Python/Node runtime lives. This build is meant for sideloading.
        targetSdk = 28
        versionCode = appVersionCode
        versionName = appVersionName
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = keystoreFile
                storePassword = System.getenv("HERMES_ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("HERMES_ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("HERMES_ANDROID_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (hasReleaseKey) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
    }

    androidResources {
        // payload.zip is already deflated; storing it avoids a second pass and lets
        // the installer stream it straight out of the APK.
        noCompress += listOf("zip")
    }

    lint {
        // targetSdk 28 is intentional (see above).
        disable += listOf("ExpiredTargetSdkVersion", "OldTargetApi")
        checkReleaseBuilds = false
        abortOnError = false
    }
}
