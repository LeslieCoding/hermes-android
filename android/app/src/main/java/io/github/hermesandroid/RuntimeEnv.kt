package io.github.hermesandroid

import android.content.Context
import org.json.JSONObject
import java.io.File

/** Builds the environment and command line that start `hermes dashboard` on-device. */
object RuntimeEnv {

    fun home(context: Context): File = File(context.filesDir, "home").apply { mkdirs() }

    fun hermesHome(context: Context): File = File(home(context), ".hermes").apply { mkdirs() }

    fun logDir(context: Context): File = File(context.filesDir, "logs").apply { mkdirs() }

    fun environment(context: Context, root: File, manifest: JSONObject, token: String): Map<String, String> {
        val r = root.absolutePath
        val launcher = manifest.getJSONObject("launcher")
        val prefix = "$r/" + manifest.getJSONObject("placeholders").optString("prefix_dir", "prefix")
        val home = home(context)
        val tmp = File(context.cacheDir, "tmp").apply { mkdirs() }
        File(prefix, "tmp").mkdirs()

        fun dirs(key: String) = buildList {
            val arr = manifest.optJSONArray(key) ?: return@buildList
            for (i in 0 until arr.length()) add("$r/" + arr.getString(i))
        }

        val python = "$r/" + launcher.getString("python")
        val repo = "$r/" + launcher.getString("repo")
        val site = "$r/" + launcher.getString("site")
        val env = linkedMapOf(
            "HOME" to home.absolutePath,
            "HERMES_HOME" to hermesHome(context).absolutePath,
            "PREFIX" to prefix,
            "TMPDIR" to tmp.absolutePath,
            "LD_LIBRARY_PATH" to dirs("ld_library_path").joinToString(":"),
            "PATH" to (dirs("path") + listOf("/system/bin", "/system/xbin", "/vendor/bin")).joinToString(":"),
            "SHELL" to "/system/bin/sh",
            "TERM" to "xterm-256color",
            "COLORTERM" to "truecolor",
            "LANG" to "en_US.UTF-8",
            "PYTHONUTF8" to "1",
            "PYTHONIOENCODING" to "utf-8",
            "PYTHONUNBUFFERED" to "1",
            "PYTHONPATH" to "$repo:$site",
            "PYTHONPYCACHEPREFIX" to File(home, ".cache/hermes-pycache").absolutePath,
            "HERMES_SITE" to site,
            "HERMES_PYTHON" to python,
            "HERMES_PYTHON_SRC_ROOT" to repo,
            "HERMES_RUNTIME_DIR" to "$r/tools",
            "HERMES_DASHBOARD_SESSION_TOKEN" to token,
            "HERMES_ANDROID_APP" to BuildConfig.VERSION_NAME,
            // Android keeps its CA store in OpenSSL hashed-directory format.
            "SSL_CERT_DIR" to "/system/etc/security/cacerts",
        )
        manifest.optString("node").takeIf { it.isNotEmpty() && it != "null" }?.let { env["HERMES_NODE"] = "$r/$it" }
        manifest.optString("web_dist").takeIf { it.isNotEmpty() && it != "null" }?.let { env["HERMES_WEB_DIST"] = "$r/$it" }
        manifest.optString("cert_file").takeIf { it.isNotEmpty() && it != "null" }?.let {
            env["SSL_CERT_FILE"] = "$r/$it"
            env["REQUESTS_CA_BUNDLE"] = "$r/$it"
        }
        return env
    }

    /** Same bootstrap the upstream `bin/hermes` launcher runs, minus the shell wrapper. */
    fun command(root: File, manifest: JSONObject, port: Int): List<String> {
        val python = root.absolutePath + "/" + manifest.getJSONObject("launcher").getString("python")
        val code = "import os, site, sys; sys.argv[0]='hermes'; " +
            "site.addsitedir(os.environ['HERMES_SITE']); " +
            "from hermes_cli.main import main; sys.exit(main())"
        return listOf(
            python, "-P", "-c", code,
            "dashboard", "--host", "127.0.0.1", "--port", port.toString(), "--no-open",
        )
    }

    /** Environment variables inherited from the app process that must not leak into Hermes. */
    val SCRUB = listOf("PYTHONHOME", "PYTHONSTARTUP", "LD_PRELOAD", "CLASSPATH")
}
