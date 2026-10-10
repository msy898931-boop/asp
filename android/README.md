# ASTRA Android app

A real installable `.apk` for the phone. It is a thin WebView shell around the
same web app in `../mobile/`, so the two can never drift apart: the APK shows
exactly what the browser shows.

## Why a WebView shell

The app is read-only and lives on a server the phone reaches over the network.
Wrapping the web app means one codebase, instant updates (change the server,
every phone sees it), and no Play Store account. The shell only adds what a web
page cannot: a launcher icon, a full-screen window, and a native error screen
that lets you change the server address on the device.

## Build

No Gradle, no Android Studio. A JDK and the Android SDK build-tools are enough:

```bash
export JAVA_HOME=/path/to/jdk-17
export ANDROID_HOME=/path/to/android-sdk        # needs build-tools;34.0.0 + platforms;android-34
android/build.sh --url https://your-server/ --name ASTRA
```

Output: `android/dist/astra.apk`.

The server URL is baked into the build, but it is not permanent: if the phone
cannot reach the server it shows "Can't reach the ASTRA server", and tapping
that screen lets you type a new address. So you can re-point an installed app
without rebuilding.

## Signing

The first build creates `android/astra.keystore` (self-signed, local). Android
only installs an update over an existing app when the signature matches, so
keep that file. It is git-ignored on purpose — a keystore is a secret.

## Install on the phone

1. Get `astra.apk` onto the phone. Download it from the mobile server at
   `/astra.apk`, from the repo's rolling **android** release, or copy it over
   USB. (The repository is private, so a GitHub release link only works when
   you are signed in — the mobile-server copy is the simplest path.)
2. Open it. Android asks to allow installs from this source — allow it.
3. Install, then open **ASTRA** from the home screen.
4. On first launch the app asks for your server address (for example
   `http://your-host:12001/`). Enter it once; the app remembers it. You can
   change it later from the error screen.

Requirements: Android 5.0+ (API 21). The Moto G7 (Android 9) is well within
range.

## What it does not do

It does not place trades, and it holds no credentials. It reads the same
shadow-mode API the web app reads, over plain HTTP on your own network.
