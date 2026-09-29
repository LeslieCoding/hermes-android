package io.github.hermesandroid

import android.os.Process
import android.system.ErrnoException
import android.system.Os
import android.system.OsConstants
import java.io.File

/** Minimal /proc helpers to find and stop Hermes processes (the runtime and its children). */
object ProcUtil {

    data class Proc(val pid: Int, val ppid: Int, val cmdline: String)

    fun list(): List<Proc> {
        val me = Process.myPid()
        val result = ArrayList<Proc>()
        val entries = File("/proc").list() ?: return result
        for (name in entries) {
            val pid = name.toIntOrNull() ?: continue
            if (pid == me) continue
            val stat = try {
                File("/proc/$pid/stat").readText()
            } catch (_: Exception) {
                continue
            }
            // The comm field may contain spaces/parens; ppid is the 2nd field after the last ')'.
            val tail = stat.substringAfterLast(')').trim().split(' ')
            val ppid = tail.getOrNull(1)?.toIntOrNull() ?: continue
            val cmd = try {
                File("/proc/$pid/cmdline").readBytes().toString(Charsets.UTF_8).replace('\u0000', ' ').trim()
            } catch (_: Exception) {
                ""
            }
            result.add(Proc(pid, ppid, cmd))
        }
        return result
    }

    fun descendants(root: Int, procs: List<Proc> = list()): List<Int> {
        val children = procs.groupBy { it.ppid }
        val out = ArrayList<Int>()
        val queue = ArrayDeque<Int>().apply { add(root) }
        while (queue.isNotEmpty()) {
            val pid = queue.removeFirst()
            for (child in children[pid].orEmpty()) {
                out.add(child.pid)
                queue.add(child.pid)
            }
        }
        return out
    }

    /** Direct children of this app process whose command line starts with [exe]. */
    fun findChild(exe: String): Int? =
        list().firstOrNull { it.ppid == Process.myPid() && it.cmdline.startsWith(exe) }?.pid

    fun alive(pid: Int): Boolean = pid > 0 && File("/proc/$pid").exists()

    fun signal(pid: Int, sig: Int) {
        if (pid <= 0 || pid == Process.myPid()) return
        try {
            Os.kill(pid, sig)
        } catch (_: ErrnoException) {
        }
    }

    /**
     * Stop [pid] and everything it spawned: SIGTERM first so Hermes can shut down
     * cleanly, SIGKILL whatever is still around after [graceMs].
     */
    fun stopTree(pid: Int, graceMs: Long = 8000) {
        if (pid <= 0) return
        val tree = descendants(pid)
        signal(pid, OsConstants.SIGTERM)
        val deadline = System.currentTimeMillis() + graceMs
        while (alive(pid) && System.currentTimeMillis() < deadline) Thread.sleep(100)
        for (child in tree) signal(child, OsConstants.SIGKILL)
        signal(pid, OsConstants.SIGKILL)
    }

    /** Kill leftovers from an earlier run (e.g. after the app process was killed). */
    fun sweep(rootPath: String): Int {
        var killed = 0
        for (p in list()) {
            if (p.cmdline.contains(rootPath)) {
                signal(p.pid, OsConstants.SIGKILL)
                killed++
            }
        }
        return killed
    }
}
