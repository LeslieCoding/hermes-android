package io.github.hermesandroid

import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CopyOnWriteArraySet

/** Process-wide state of the on-device Hermes runtime, shared by the service and the UI. */
object HermesRuntime {

    enum class Phase { IDLE, INSTALLING, STARTING, RUNNING, STOPPING, STOPPED, ERROR }

    data class Snapshot(
        val phase: Phase = Phase.IDLE,
        val message: String = "",
        /** 0..1 while INSTALLING, otherwise -1. */
        val progress: Float = -1f,
        val port: Int = 0,
        val token: String = "",
        val pid: Int = 0,
        val startedAt: Long = 0L,
        val payloadId: String = "",
        val exitCode: Int? = null,
    )

    @Volatile
    var snapshot = Snapshot()
        private set

    private val listeners = CopyOnWriteArraySet<(Snapshot) -> Unit>()

    fun update(transform: (Snapshot) -> Snapshot) {
        val next: Snapshot
        synchronized(this) {
            next = transform(snapshot)
            snapshot = next
        }
        for (listener in listeners) {
            try {
                listener(next)
            } catch (_: Throwable) {
            }
        }
    }

    fun addListener(listener: (Snapshot) -> Unit) {
        listeners.add(listener)
    }

    fun removeListener(listener: (Snapshot) -> Unit) {
        listeners.remove(listener)
    }

    // ── recent log lines (the full log is written to files/logs/hermes.log) ──

    private const val MAX_LINES = 2000
    private val lines = ArrayDeque<String>()

    fun appendLog(line: String) {
        android.util.Log.i("Hermes", line)
        synchronized(lines) {
            lines.addLast(line)
            while (lines.size > MAX_LINES) lines.removeFirst()
        }
    }

    fun recentLog(max: Int): List<String> = synchronized(lines) {
        val from = (lines.size - max).coerceAtLeast(0)
        lines.toList().subList(from, lines.size)
    }

    /** Status for the web UI. The session token is included only for same-origin callers. */
    fun statusJson(includeToken: Boolean): JSONObject {
        val s = snapshot
        return JSONObject().apply {
            put("phase", s.phase.name.lowercase())
            put("message", s.message)
            put("progress", s.progress.toDouble())
            put("port", s.port)
            put("pid", s.pid)
            put("startedAt", s.startedAt)
            put("payloadId", s.payloadId)
            put("exitCode", s.exitCode ?: JSONObject.NULL)
            put("appVersion", BuildConfig.VERSION_NAME)
            if (includeToken) put("token", s.token)
        }
    }

    fun logJson(max: Int): JSONObject = JSONObject().put("lines", JSONArray(recentLog(max)))
}
