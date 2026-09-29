package io.github.hermesandroid

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.ServerSocket
import java.net.URL
import java.security.SecureRandom
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import android.util.Base64
import kotlin.concurrent.thread

/**
 * Foreground service that owns the Hermes runtime process: unpacks the payload on
 * first run, starts `hermes dashboard` on 127.0.0.1, pumps its log, and stops the
 * whole process tree on request.
 */
class HermesService : Service() {

    private var worker: Thread? = null
    @Volatile private var process: java.lang.Process? = null
    @Volatile private var pid: Int = 0
    @Volatile private var stopping = false
    private var wakeLock: PowerManager.WakeLock? = null
    private val crashTimes = java.util.concurrent.CopyOnWriteArrayList<Long>()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIFICATION_ID, notification())
        when (intent?.action) {
            ACTION_STOP -> {
                thread(name = "hermes-stop") {
                    stopRuntime()
                    stopForegroundCompat()
                    stopSelf()
                }
                return START_NOT_STICKY
            }
            ACTION_RESTART -> thread(name = "hermes-restart") {
                stopRuntime()
                worker?.join()
                crashTimes.clear()
                launch()
            }
            else -> launch()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        HermesRuntime.removeListener(notifier)
        thread(name = "hermes-destroy") { stopRuntime() }
        releaseWakeLock()
        super.onDestroy()
    }

    private val notifier: (HermesRuntime.Snapshot) -> Unit = { updateNotification() }

    override fun onCreate() {
        super.onCreate()
        HermesRuntime.addListener(notifier)
    }

    @Synchronized
    private fun launch() {
        if (worker?.isAlive == true) return
        stopping = false
        worker = thread(name = "hermes-runtime") { runRuntime() }
    }

    private fun runRuntime() {
        val installer = PayloadInstaller(this)
        try {
            if (!installer.hasPayload()) {
                fail("这个安装包没有内置 Hermes 运行时（构建时缺少 payload）")
                return
            }
            val manifest = installer.manifest()
            val payloadId = manifest.getString("payload_id")
            HermesRuntime.update { it.copy(payloadId = payloadId, exitCode = null) }

            val swept = ProcUtil.sweep(installer.root.absolutePath)
            if (swept > 0) log("清理了 $swept 个残留进程")

            if (installer.needsInstall(manifest)) {
                val need = manifest.optLong("total_bytes", 0L) + 200L * 1024 * 1024
                val free = filesDir.usableSpace
                if (free in 1 until need) {
                    fail("存储空间不足：需要约 ${need / 1_000_000} MB，当前可用 ${free / 1_000_000} MB")
                    return
                }
                HermesRuntime.update { it.copy(phase = HermesRuntime.Phase.INSTALLING, progress = 0f, message = "首次运行，正在解压运行环境…") }
                installer.install(manifest) { p, label ->
                    HermesRuntime.update { it.copy(progress = p, message = label) }
                }
                log("运行环境已安装：$payloadId")
            }
            if (stopping) return

            val port = choosePort(Prefs.port(this))
            val token = newToken()
            HermesRuntime.update {
                it.copy(phase = HermesRuntime.Phase.STARTING, progress = -1f, port = port, token = token,
                    message = "正在启动 Hermes…", startedAt = System.currentTimeMillis())
            }
            acquireWakeLock()

            val cmd = RuntimeEnv.command(installer.root, manifest, port)
            val pb = ProcessBuilder(cmd)
                .directory(RuntimeEnv.home(this))
                .redirectErrorStream(true)
            val env = pb.environment()
            RuntimeEnv.SCRUB.forEach { env.remove(it) }
            env.putAll(RuntimeEnv.environment(this, installer.root, manifest, token))
            log("启动：hermes dashboard --port $port")
            val proc = pb.start()
            process = proc
            pid = ProcUtil.findChild(cmd[0]) ?: 0
            HermesRuntime.update { it.copy(pid = pid) }
            val pump = thread(name = "hermes-log") { pumpLog(proc) }

            if (waitHealthy(port, proc)) {
                HermesRuntime.update { it.copy(phase = HermesRuntime.Phase.RUNNING, message = "运行中") }
                log("Hermes 已就绪：http://127.0.0.1:$port")
            }
            val code = proc.waitFor()
            pump.join(2000)
            process = null
            if (stopping) {
                HermesRuntime.update { it.copy(phase = HermesRuntime.Phase.STOPPED, message = "已停止", exitCode = code, pid = 0) }
            } else {
                val killed = code == 137 || code == 9
                val hint = if (killed) "，可能被系统的后台限制结束" else ""
                val now = System.currentTimeMillis()
                crashTimes.removeIf { now - it > 10 * 60 * 1000L }
                crashTimes.add(now)
                if (crashTimes.size <= MAX_AUTO_RESTARTS) {
                    log("Hermes 进程退出（代码 $code$hint），自动重启（${crashTimes.size}/$MAX_AUTO_RESTARTS）")
                    HermesRuntime.update { it.copy(phase = HermesRuntime.Phase.STARTING, message = "正在自动重启…", exitCode = code, pid = 0) }
                    releaseWakeLock()
                    Thread.sleep(2000L * crashTimes.size)
                    if (!stopping) {
                        thread(name = "hermes-relaunch") {
                            worker?.join()
                            launch()
                        }
                    }
                    return
                }
                fail("Hermes 进程意外退出（代码 $code$hint），请查看日志", code)
            }
        } catch (t: Throwable) {
            log("错误：${t}")
            fail(t.message ?: t.toString())
        } finally {
            releaseWakeLock()
        }
    }

    private fun fail(message: String, code: Int? = null) {
        HermesRuntime.update { it.copy(phase = HermesRuntime.Phase.ERROR, message = message, exitCode = code, pid = 0, progress = -1f) }
    }

    private fun waitHealthy(port: Int, proc: java.lang.Process): Boolean {
        val deadline = System.currentTimeMillis() + 10 * 60 * 1000L
        while (System.currentTimeMillis() < deadline) {
            if (stopping || !proc.isAliveCompat()) return false
            try {
                val conn = URL("http://127.0.0.1:$port/api/status").openConnection() as HttpURLConnection
                conn.connectTimeout = 1500
                conn.readTimeout = 3000
                val code = conn.responseCode
                conn.disconnect()
                if (code in 200..499) return true
            } catch (_: Exception) {
            }
            Thread.sleep(700)
        }
        log("等待 Hermes 就绪超时")
        return false
    }

    private fun pumpLog(proc: java.lang.Process) {
        val logFile = File(RuntimeEnv.logDir(this), "hermes.log")
        if (logFile.length() > 4L * 1024 * 1024) {
            logFile.renameTo(File(logFile.parentFile, "hermes.log.1"))
        }
        FileOutputStream(logFile, true).bufferedWriter().use { out ->
            out.write("\n===== ${stamp()} start =====\n")
            try {
                proc.inputStream.bufferedReader().forEachLine { line ->
                    HermesRuntime.appendLog(line)
                    out.write(line)
                    out.write("\n")
                    out.flush()
                }
            } catch (_: Exception) {
            }
        }
    }

    private fun stopRuntime() {
        stopping = true
        val proc = process
        if (proc != null || pid > 0) {
            HermesRuntime.update { it.copy(phase = HermesRuntime.Phase.STOPPING, message = "正在停止…") }
            val target = if (pid > 0) pid else 0
            if (target > 0) ProcUtil.stopTree(target) else proc?.destroy()
            try {
                proc?.waitFor()
            } catch (_: InterruptedException) {
            }
        }
        worker?.join(5000)
        process = null
        pid = 0
        HermesRuntime.update { it.copy(phase = HermesRuntime.Phase.STOPPED, message = "已停止", pid = 0) }
    }

    private fun choosePort(preferred: Int): Int {
        for (candidate in listOf(preferred) + (9120..9199)) {
            try {
                ServerSocket(candidate, 1, InetAddress.getByName("127.0.0.1")).close()
                if (candidate != preferred) Prefs.setPort(this, candidate)
                return candidate
            } catch (_: Exception) {
            }
        }
        throw IllegalStateException("找不到可用的本地端口")
    }

    private fun newToken(): String {
        val bytes = ByteArray(32)
        SecureRandom().nextBytes(bytes)
        return Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    }

    private fun log(line: String) {
        HermesRuntime.appendLog("[app] $line")
    }

    private fun stamp() = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())

    // ── wake lock ──

    private fun acquireWakeLock() {
        if (!Prefs.keepAwake(this)) return
        if (wakeLock?.isHeld == true) return
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "hermes:runtime").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseWakeLock() {
        try {
            wakeLock?.takeIf { it.isHeld }?.release()
        } catch (_: Exception) {
        }
        wakeLock = null
    }

    // ── notification ──

    private fun notification(): Notification {
        val s = HermesRuntime.snapshot
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or pendingFlags(),
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, HermesService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or pendingFlags(),
        )
        val (title, text) = when (s.phase) {
            HermesRuntime.Phase.INSTALLING -> getString(R.string.notif_installing) to
                (if (s.progress >= 0) "${(s.progress * 100).toInt()}%" else "")
            HermesRuntime.Phase.RUNNING -> getString(R.string.notif_running) to getString(R.string.notif_running_text)
            HermesRuntime.Phase.ERROR -> getString(R.string.notif_error) to s.message
            else -> getString(R.string.notif_starting) to s.message
        }
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, HermesApp.CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this).setPriority(Notification.PRIORITY_LOW)
        }
        builder.setSmallIcon(R.drawable.ic_stat_hermes)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(s.phase != HermesRuntime.Phase.ERROR)
            .setOnlyAlertOnce(true)
            .addAction(
                Notification.Action.Builder(
                    android.graphics.drawable.Icon.createWithResource(this, R.drawable.ic_stat_hermes),
                    getString(R.string.action_stop),
                    stop,
                ).build(),
            )
        if (s.phase == HermesRuntime.Phase.INSTALLING && s.progress >= 0) {
            builder.setProgress(100, (s.progress * 100).toInt(), false)
        }
        return builder.build()
    }

    private var lastNotified = ""

    private fun updateNotification() {
        val s = HermesRuntime.snapshot
        val key = "${s.phase}|${(s.progress * 50).toInt()}|${s.message}"
        if (key == lastNotified) return
        lastNotified = key
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
        nm.notify(NOTIFICATION_ID, notification())
    }

    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= 24) {
            stopForeground(Service.STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    private fun pendingFlags(): Int = if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0

    companion object {
        const val ACTION_START = "io.github.hermesandroid.START"
        const val ACTION_STOP = "io.github.hermesandroid.STOP"
        const val ACTION_RESTART = "io.github.hermesandroid.RESTART"
        private const val NOTIFICATION_ID = 42
        private const val MAX_AUTO_RESTARTS = 3

        fun send(context: Context, action: String) {
            val intent = Intent(context, HermesService::class.java).setAction(action)
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(intent) else context.startService(intent)
        }
    }
}

private fun java.lang.Process.isAliveCompat(): Boolean = try {
    exitValue()
    false
} catch (_: IllegalThreadStateException) {
    true
}
