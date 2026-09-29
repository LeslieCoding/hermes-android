#!/usr/bin/env bash
# Install the release APK on an emulator, launch it and wait for the on-device
# Hermes to come up. Screenshots, UI dump and logcat go to ./e2e.
set -u
PKG=io.github.hermesandroid
mkdir -p e2e
APK=$(ls dist/*.apk | head -1)
{
  echo "abi: $(adb shell getprop ro.product.cpu.abilist)"
  echo "native bridge: $(adb shell getprop ro.dalvik.vm.native.bridge) exec=$(adb shell getprop ro.enable.native.bridge.exec)"
  echo "sdk: $(adb shell getprop ro.build.version.sdk)"
} | tee e2e/summary.txt
adb shell settings put global package_verifier_enable 0 >/dev/null 2>&1
echo "install: $(adb install -r -g "$APK" 2>&1 | tail -1)" | tee -a e2e/summary.txt
adb logcat -c
adb shell am start -W -n "$PKG/.MainActivity" | tail -2
ok=0
start=$(date +%s)
for i in $(seq 1 150); do
  sleep 10
  adb forward tcp:9119 tcp:9119 >/dev/null 2>&1
  if curl -sf -m 5 http://127.0.0.1:9119/api/status > e2e/status.json; then ok=1; break; fi
  if [ $((i % 9)) -eq 0 ]; then adb exec-out screencap -p > "e2e/wait-$i.png"; fi
  if ! adb shell pidof "$PKG" >/dev/null; then echo "app process died" | tee -a e2e/summary.txt; break; fi
done
echo "server up: $ok after $(( $(date +%s) - start ))s" | tee -a e2e/summary.txt
head -c 300 e2e/status.json 2>/dev/null >> e2e/summary.txt; echo >> e2e/summary.txt
sleep 25
adb exec-out screencap -p > e2e/1-chat.png
adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1 && adb pull /sdcard/ui.xml e2e/ui.xml >/dev/null 2>&1
if grep -q "已连接\|还没有配置\|glm\|deepseek" e2e/ui.xml 2>/dev/null; then connected=1; else connected=0; fi
echo "ui shows connected: $connected" | tee -a e2e/summary.txt
grep -o 'text="[^"]\+"' e2e/ui.xml 2>/dev/null | head -40 >> e2e/summary.txt
adb logcat -d -v brief Hermes:V HermesWeb:V AndroidRuntime:E ActivityManager:W '*:S' > e2e/logcat.txt
[ "$ok" = 1 ] && [ "$connected" = 1 ]
