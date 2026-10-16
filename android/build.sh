#!/usr/bin/env bash
# Build a signed, installable ASTRA APK without Gradle.
#
# The app is a single Activity around a WebView, so it needs no library
# dependencies and no AAPT2 resource linking beyond what `aapt2` and `javac`
# already do. Keeping it Gradle-free means a plain JDK + Android build-tools is
# enough to produce a release APK, which is all a personal build needs.
#
# Usage:
#   android/build.sh --url http://192.168.1.50:12001/ [--name ASTRA] [--key SECRET]
#
# --key bakes the gate key into a *personal* build so the app opens straight to
# the data with no typing. Leave it off for the public rolling release: that APK
# is a public download, and a key in it would hand the gate to everyone.
#
# Requires: JAVA_HOME (JDK 17+) and ANDROID_HOME with build-tools + platform.
# The APK is signed with a local keystore (android/astra.keystore), created on
# first run. Keep that file: Android only installs updates signed with the same
# key.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

: "${JAVA_HOME:?set JAVA_HOME to a JDK 17+}"
: "${ANDROID_HOME:?set ANDROID_HOME to your Android SDK}"
export PATH="$JAVA_HOME/bin:$PATH"

BT_VER="${BT_VER:-34.0.0}"
PLATFORM_VER="${PLATFORM_VER:-34}"
BUILD_TOOLS="$ANDROID_HOME/build-tools/$BT_VER"
ANDROID_JAR="$ANDROID_HOME/platforms/android-$PLATFORM_VER/android.jar"

# The phone app is a shell around the hosted web app, so the default target is
# the live server. A blank --url (or no ASTRA_URL variable) must NOT fall back
# to loopback: 127.0.0.1 on a phone is the phone itself, which made the APK
# open an empty error screen and look broken. --url still overrides.
SERVER_URL="https://astra-xlp9.onrender.com/"
APP_NAME="ASTRA"
ACCESS_KEY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --url) [ -n "$2" ] && SERVER_URL="$2"; shift 2 ;;
    --name) APP_NAME="$2"; shift 2 ;;
    --key) ACCESS_KEY="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

APP="$HERE/app"
SRC="$APP/src/main"
BUILD="$HERE/build"
OUT="$HERE/dist"
KEYSTORE="$HERE/astra.keystore"
STOREPASS="astra-local"

for tool in aapt2 d8 zipalign apksigner; do
  [ -x "$BUILD_TOOLS/$tool" ] || { echo "missing build-tool: $tool" >&2; exit 1; }
done
[ -f "$ANDROID_JAR" ] || { echo "missing $ANDROID_JAR" >&2; exit 1; }

rm -rf "$BUILD"
mkdir -p "$BUILD/compiled-res" "$BUILD/gen" "$BUILD/classes" "$BUILD/dex" "$OUT"

echo "==> icons"
python3 "$HERE/make_android_icons.py"

echo "==> stage sources (bake in URL and name)"
STAGE="$BUILD/stage"
mkdir -p "$STAGE/java/ai/astra/mobile" "$STAGE/res/values"
cp "$SRC/java/ai/astra/mobile/MainActivity.java" "$STAGE/java/ai/astra/mobile/"
python3 - "$STAGE/java/ai/astra/mobile/MainActivity.java" "$SERVER_URL" "$ACCESS_KEY" <<'PY'
import sys
path, url, key = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()

def bake(text, const, value):
    marker = 'private static final String %s = "' % const
    i = text.index(marker) + len(marker)
    j = text.index('";', i)
    return text[:i] + value + text[j:]

text = bake(text, "DEFAULT_URL", url)
text = bake(text, "DEFAULT_KEY", key)
open(path, "w", encoding="utf-8").write(text)
PY
sed "s|<string name=\"app_name\">.*</string>|<string name=\"app_name\">$APP_NAME</string>|" \
  "$SRC/res/values/strings.xml" > "$STAGE/res/values/strings.xml"
cp "$SRC/res/values/styles.xml" "$STAGE/res/values/"
cp "$SRC/res/xml/network_security_config.xml" "$STAGE/res/xml/network_security_config.xml" 2>/dev/null || {
  mkdir -p "$STAGE/res/xml"; cp "$SRC/res/xml/network_security_config.xml" "$STAGE/res/xml/"; }
cp -r "$SRC/res"/mipmap-* "$STAGE/res/"

echo "==> resources (aapt2)"
"$BUILD_TOOLS/aapt2" compile --dir "$STAGE/res" -o "$BUILD/compiled-res/res.zip"
"$BUILD_TOOLS/aapt2" link \
  -o "$BUILD/base.apk" \
  -I "$ANDROID_JAR" \
  --manifest "$SRC/AndroidManifest.xml" \
  --java "$BUILD/gen" \
  --min-sdk-version 21 \
  --target-sdk-version "$PLATFORM_VER" \
  --version-code 1 \
  --version-name "1.0" \
  -R "$BUILD/compiled-res/res.zip" \
  --auto-add-overlay

echo "==> java"
# android.jar goes on the classpath, not the bootclasspath: the JDK's own
# platform classes are needed to resolve lambda metafactory (desugared later by
# d8), which android.jar alone does not provide.
javac -source 8 -target 8 -encoding UTF-8 \
  -classpath "$ANDROID_JAR" \
  -d "$BUILD/classes" \
  $(find "$STAGE/java" "$BUILD/gen" -name '*.java')

"$BUILD_TOOLS/d8" --lib "$ANDROID_JAR" --min-api 21 \
  --output "$BUILD/dex" $(find "$BUILD/classes" -name '*.class')

echo "==> package"
cp "$BUILD/base.apk" "$BUILD/unsigned.apk"
python3 - "$BUILD/unsigned.apk" "$BUILD/dex/classes.dex" <<'PY'
import sys, zipfile
apk, dex = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk, "a", zipfile.ZIP_DEFLATED) as z:
    z.write(dex, "classes.dex")
PY

if [ ! -f "$KEYSTORE" ]; then
  echo "==> keystore (first run)"
  keytool -genkeypair -v \
    -keystore "$KEYSTORE" \
    -alias astra \
    -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass "$STOREPASS" -keypass "$STOREPASS" \
    -dname "CN=ASTRA Mobile, OU=Personal, O=ASTRA, L=-, S=-, C=-" >/dev/null
fi

"$BUILD_TOOLS/zipalign" -f 4 "$BUILD/unsigned.apk" "$BUILD/aligned.apk"
"$BUILD_TOOLS/apksigner" sign \
  --ks "$KEYSTORE" --ks-pass "pass:$STOREPASS" --key-pass "pass:$STOREPASS" \
  --ks-key-alias astra --out "$OUT/astra.apk" "$BUILD/aligned.apk"
"$BUILD_TOOLS/apksigner" verify --print-certs "$OUT/astra.apk" | head -3

echo
echo "APK ready: $OUT/astra.apk"
echo "Server URL baked in: $SERVER_URL"
if [ -n "$ACCESS_KEY" ]; then
  echo "Access key baked in: yes (personal build — do not publish this APK)"
else
  echo "Access key baked in: no (the gate, if on, asks once on first launch)"
fi
echo "To change the URL after install: open the app, tap the error screen."
