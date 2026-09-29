package io.github.hermesandroid

import android.content.Context
import android.system.ErrnoException
import android.system.Os
import android.system.OsConstants
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.util.zip.ZipInputStream

/**
 * Unpacks the relocatable Hermes runtime shipped in assets/payload/ into the app's
 * private files dir and fixes up what a zip cannot carry (symlinks, exec bits, and
 * the absolute install path baked into text files). See scripts/relocate_payload.py.
 */
class PayloadInstaller(private val context: Context) {

    val root: File get() = File(context.filesDir, "hermes-agent")

    fun hasPayload(): Boolean = try {
        context.assets.open(MANIFEST_ASSET).close()
        true
    } catch (_: IOException) {
        false
    }

    fun manifest(): JSONObject =
        context.assets.open(MANIFEST_ASSET).use { JSONObject(String(it.readBytes(), Charsets.UTF_8)) }

    fun installedId(): String? =
        File(root, ID_FILE).takeIf { it.isFile }?.readText()?.trim()

    fun needsInstall(manifest: JSONObject): Boolean =
        installedId() != manifest.getString("payload_id") || !File(root, manifest.getJSONObject("launcher").getString("python")).exists()

    /** Blocking; call from a worker thread. [progress] gets 0..1 and a short label. */
    fun install(manifest: JSONObject, progress: (Float, String) -> Unit) {
        val files = context.filesDir
        val staging = File(files, "hermes-agent.staging")
        val old = File(files, "hermes-agent.old")
        FileTools.deleteTree(staging)
        FileTools.deleteTree(old)
        staging.mkdirs()

        val total = manifest.optLong("total_bytes", 1L).coerceAtLeast(1L)
        var written = 0L
        var lastReport = 0L
        val buffer = ByteArray(256 * 1024)
        progress(0f, "解压运行环境")

        ZipInputStream(BufferedInputStream(context.assets.open(ZIP_ASSET), 1 shl 20)).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                val name = entry.name
                checkEntryName(name)
                val out = File(staging, name)
                if (entry.isDirectory) {
                    out.mkdirs()
                    continue
                }
                out.parentFile?.mkdirs()
                FileOutputStream(out).use { fos ->
                    while (true) {
                        val n = zip.read(buffer)
                        if (n < 0) break
                        fos.write(buffer, 0, n)
                        written += n
                    }
                }
                if (written - lastReport > 4L * 1024 * 1024) {
                    lastReport = written
                    progress((written.toFloat() / total).coerceAtMost(0.97f), "解压运行环境")
                }
            }
        }

        // Paths inside the payload must point at the FINAL location, not the staging dir.
        val finalRoot = root.absolutePath
        val placeholders = manifest.getJSONObject("placeholders")
        val rootPh = placeholders.getString("root")
        val prefixPh = placeholders.getString("prefix")
        val prefixDir = "$finalRoot/" + placeholders.optString("prefix_dir", "prefix")
        fun resolve(value: String) = value.replace(rootPh, finalRoot).replace(prefixPh, prefixDir)

        progress(0.97f, "配置运行环境")
        val rewrite = manifest.getJSONArray("rewrite")
        for (i in 0 until rewrite.length()) {
            val file = File(staging, rewrite.getString(i))
            if (!file.isFile) continue
            // ISO-8859-1 round-trips arbitrary bytes; the placeholders are ASCII.
            val text = String(file.readBytes(), Charsets.ISO_8859_1)
            val fixed = resolve(text)
            if (fixed != text) file.writeBytes(fixed.toByteArray(Charsets.ISO_8859_1))
        }

        val links = manifest.getJSONArray("symlinks")
        for (i in 0 until links.length()) {
            val pair = links.getJSONArray(i)
            val link = File(staging, pair.getString(0))
            val target = resolve(pair.getString(1))
            link.parentFile?.mkdirs()
            if (FileTools.exists(link)) FileTools.deleteTree(link)
            try {
                Os.symlink(target, link.absolutePath)
            } catch (e: ErrnoException) {
                throw IOException("symlink ${pair.getString(0)} -> $target failed: ${e.message}", e)
            }
        }

        val executables = manifest.getJSONArray("executables")
        for (i in 0 until executables.length()) {
            val file = File(staging, executables.getString(i))
            if (file.isFile) {
                try {
                    Os.chmod(file.absolutePath, "755".toInt(8))
                } catch (_: ErrnoException) {
                    file.setExecutable(true, false)
                }
            }
        }

        File(staging, ID_FILE).writeText(manifest.getString("payload_id"))

        progress(0.99f, "切换运行环境")
        if (root.exists() && !root.renameTo(old)) {
            FileTools.deleteTree(root)
        }
        if (!staging.renameTo(root)) throw IOException("无法启用新的运行环境")
        FileTools.deleteTree(old)
        progress(1f, "运行环境已就绪")
    }

    private fun checkEntryName(name: String) {
        if (name.startsWith("/") || name.split('/').any { it == ".." }) {
            throw IOException("unsafe payload entry: $name")
        }
    }

    companion object {
        const val MANIFEST_ASSET = "payload/payload.json"
        const val ZIP_ASSET = "payload/payload.zip"
        private const val ID_FILE = ".payload_id"
    }
}

object FileTools {
    /** lstat-based existence check that also sees dangling symlinks. */
    fun exists(file: File): Boolean = try {
        Os.lstat(file.absolutePath)
        true
    } catch (_: ErrnoException) {
        false
    }

    private fun isSymlink(file: File): Boolean = try {
        OsConstants.S_ISLNK(Os.lstat(file.absolutePath).st_mode)
    } catch (_: ErrnoException) {
        false
    }

    /** Recursive delete that never follows symlinks (File.deleteRecursively would). */
    fun deleteTree(file: File) {
        if (!exists(file)) return
        if (!isSymlink(file) && file.isDirectory) {
            file.listFiles()?.forEach { deleteTree(it) }
        }
        file.delete()
    }
}
