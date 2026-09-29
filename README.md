# Hermes for Android

把 [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) 完整地装进一部安卓手机：一个 APK，装好即用，**不需要电脑、不需要 Termux、不需要 root**。Hermes 智能体（Python 内核、工具、技能、记忆、定时任务、控制台）全部在手机本地运行，只有调用大模型 API 时才联网。

> 这是社区移植，不是 Nous Research 官方应用。上游项目采用 MIT 许可。

## 它是怎么工作的

```
┌──────────────────────── APK ────────────────────────┐
│  MainActivity (WebView)          HermesService       │
│  ├─ 对话：手机版聊天界面   ◀──▶  前台服务，负责：     │
│  ├─ 控制台：官方 Dashboard        · 首次运行解压运行时 │
│  └─ 运行：状态 / 日志 / 开关      · 启动 hermes dashboard│
│            │  http/ws 127.0.0.1    · 停止整个进程树     │
│            ▼                                         │
│  assets/payload/payload.zip  ──解压──▶ files/hermes-agent│
│   官方 Android 运行时：bionic Python 3.14、              │
│   android_24_arm64_v8a 原生 wheel、Node、ripgrep、ffmpeg │
└──────────────────────────────────────────────────────┘
```

- **运行时来自上游官方构建**：Nous Research 在 CI 里为 Android（aarch64）编译了整套运行时，并以签名的 APT 软件包发布（原本给 Termux 用）。构建脚本会下载它、校验仓库公钥指纹、`InRelease` 签名和包的 SHA-256，然后重定位到 App 私有目录可用的形式（`scripts/relocate_payload.py`）。
- **App 启动时**，前台服务在 `127.0.0.1` 上运行 `hermes dashboard`（它同时提供 Agent 网关 `/api/ws`），会话令牌每次启动随机生成。
- **对话页**是为手机写的界面（`android/app/src/main/assets/ui/`），通过与官方桌面版相同的 JSON-RPC 网关协议通信：流式回复、思考过程、工具调用卡片、危险命令审批、澄清提问、会话列表、图片/PDF/文件附件、分享到 Hermes。
- **控制台页**直接嵌入上游的 Web Dashboard，用来配置模型服务商和 API Key、技能、MCP、定时任务、消息渠道等。

## 使用

1. 安装 APK（需要在系统里允许“安装未知来源应用”）。只支持 **64 位 ARM（arm64-v8a）、Android 7.0 及以上**。
2. 第一次打开会解压运行环境，大约 1–3 分钟，只需一次。
3. 打开 **控制台 → Models / Keys**，添加一个模型服务商和 API Key（DeepSeek、通义、Kimi、智谱、OpenRouter、OpenAI、Anthropic 等，按上游支持为准）。
4. 回到 **对话** 开始使用。

建议在「运行」页里关闭电池优化；Android 可能会在后台暂停或结束长时间运行的进程，这点和 Termux 上的限制一样。

### 已知限制

- 与上游 Termux 包相同：不含 Electron、本地 Chromium、桌面“电脑操作”类工具，也没有 Docker；上游标注为仅支持 Linux 的可选依赖或技能在 Android 上可能不可用。
- 没有 `bash`、`git` 等完整 Linux 用户态；终端类工具使用系统自带的 `/system/bin/sh`（toybox）。
- `targetSdkVersion` 刻意设为 28（和 Termux 一样）：从 API 29 开始，Android 禁止执行 App 可写目录里的程序，而内置运行时正是放在那里。因此这个 APK 适合自行安装，不能上架 Google Play；部分系统安装时会提示“此应用专为旧版 Android 打造”，属正常现象。
- 上游文档目前标注 Termux 包“正在修复中”。如果 stable 渠道的包有问题，可以在手动构建时选 canary 渠道，或固定某个已知可用的版本。

## 构建

本仓库用 GitHub Actions 出包（`.github/workflows/build-apk.yml`），推送到 `main` 即自动构建，在 Actions 运行页的 Artifacts 里下载 APK。推送 `v*` 标签会同时发布到 Releases。

手动构建（Actions → Build APK → Run workflow）可以选择：

- **channel**：`stable`（默认）或 `canary`
- **version**：固定某个运行时版本（Debian 版本号），留空取最新

构建流程：

1. `scripts/fetch_payload.py` 下载并校验官方 Android 运行时包
2. 取出包内记录的上游提交号，检出同一版本的源码并构建 Dashboard 前端
3. `scripts/relocate_payload.py` 打平目录、改写路径、生成 `payload.zip` 和清单，并检查动态库依赖是否齐全
4. Gradle 打包 APK

### 签名

没有配置签名密钥时，CI 会用临时调试密钥签名——**每次构建的签名都不同，新版本无法覆盖安装旧版本**（只能卸载重装，数据会丢失）。建议一开始就配置固定密钥：

```bash
scripts/make_keystore.sh          # 需要 JDK 的 keytool
```

按提示把输出的值添加到仓库 **Settings → Secrets and variables → Actions**：
`ANDROID_KEYSTORE_BASE64`、`ANDROID_KEYSTORE_PASSWORD`、`ANDROID_KEY_ALIAS`（可选 `ANDROID_KEY_PASSWORD`）。

### 本地调试界面

不需要手机也能调对话界面：`tools/mock_gateway.py` 模拟了 Dashboard 和网关（流式回复、工具调用、审批请求、会话列表）。

```bash
python3 tools/mock_gateway.py android/app/src/main/assets/ui 9119
# 浏览器打开 http://127.0.0.1:9119/__android/index.html
```

## 目录

```
android/                     Android 工程（Kotlin，无第三方依赖）
  app/src/main/java/...      MainActivity、HermesService、PayloadInstaller 等
  app/src/main/assets/ui/    手机版界面（原生 JS，无需构建）
scripts/fetch_payload.py     下载并校验官方运行时
scripts/relocate_payload.py  把运行时重定位为 App 可用的 payload
scripts/make_keystore.sh     生成签名密钥
tools/mock_gateway.py        界面调试用的模拟服务
```

## 许可

本仓库代码以 MIT 许可发布。APK 中打包的 Hermes Agent 及其依赖遵循各自的许可证（上游 hermes-agent 为 MIT）。
