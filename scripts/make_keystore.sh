#!/usr/bin/env bash
# 生成固定的 APK 签名密钥，并打印需要填到 GitHub 仓库 Secrets 里的值。
# 用法: scripts/make_keystore.sh [输出文件，默认 hermes-release.jks]
set -euo pipefail
OUT="${1:-hermes-release.jks}"
ALIAS="hermes"
if [ -e "$OUT" ]; then echo "$OUT 已存在，不会覆盖" >&2; exit 1; fi
read -rsp "设置密钥库密码（至少 6 位）: " PASS; echo
keytool -genkeypair -v -keystore "$OUT" -storetype JKS -alias "$ALIAS" \
  -keyalg RSA -keysize 4096 -validity 36500 \
  -storepass "$PASS" -keypass "$PASS" \
  -dname "CN=Hermes Android, O=Personal"
echo
echo "请在 GitHub 仓库 Settings → Secrets and variables → Actions 中添加："
echo "  ANDROID_KEYSTORE_BASE64   = 下面这一长串"
echo "  ANDROID_KEYSTORE_PASSWORD = 你刚才设置的密码"
echo "  ANDROID_KEY_ALIAS         = $ALIAS"
echo
base64 -w0 "$OUT" 2>/dev/null || base64 "$OUT" | tr -d '\n'
echo
echo
echo "请妥善保管 $OUT 和密码：丢失后新版本将无法覆盖安装旧版本。"
