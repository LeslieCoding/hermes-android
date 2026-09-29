package io.github.hermesandroid

import android.content.Context
import android.net.Uri
import android.webkit.WebResourceResponse
import java.io.ByteArrayInputStream
import java.io.IOException

/**
 * Serves the mobile shell (assets/ui) under http://127.0.0.1:<port>/__android/ so it
 * shares an origin with the Hermes dashboard: REST and /api/ws calls are same-origin,
 * and the session token in status.json cannot be read cross-origin (no CORS headers).
 * Everything outside /__android/ goes to the real server.
 */
object AssetServer {

    const val PREFIX = "/__android/"

    fun isLocal(uri: Uri?, port: Int): Boolean =
        uri != null && uri.scheme == "http" &&
            (uri.host == "127.0.0.1" || uri.host == "localhost") && uri.port == port

    fun handle(context: Context, uri: Uri, port: Int): WebResourceResponse? {
        if (!isLocal(uri, port)) return null
        val path = uri.path ?: return null
        if (!path.startsWith(PREFIX)) return null
        val rel = path.removePrefix(PREFIX).ifEmpty { "index.html" }
        return when (rel) {
            "status.json" -> json(HermesRuntime.statusJson(includeToken = true).toString())
            "log.json" -> {
                val n = uri.getQueryParameter("n")?.toIntOrNull()?.coerceIn(1, 2000) ?: 400
                json(HermesRuntime.logJson(n).toString())
            }
            else -> asset(context, rel)
        }
    }

    private fun headers() = mapOf(
        "Cache-Control" to "no-store",
        "X-Content-Type-Options" to "nosniff",
    )

    private fun json(body: String) = WebResourceResponse(
        "application/json", "utf-8", 200, "OK", headers(),
        ByteArrayInputStream(body.toByteArray(Charsets.UTF_8)),
    )

    private fun asset(context: Context, rel: String): WebResourceResponse {
        if (rel.split('/').any { it == ".." }) return notFound()
        return try {
            val stream = context.assets.open("ui/$rel")
            WebResourceResponse(mime(rel), "utf-8", 200, "OK", headers(), stream)
        } catch (_: IOException) {
            notFound()
        }
    }

    private fun notFound() = WebResourceResponse(
        "text/plain", "utf-8", 404, "Not Found", headers(), ByteArrayInputStream(ByteArray(0)),
    )

    private fun mime(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        "html" -> "text/html"
        "js", "mjs" -> "text/javascript"
        "css" -> "text/css"
        "json" -> "application/json"
        "svg" -> "image/svg+xml"
        "png" -> "image/png"
        "woff2" -> "font/woff2"
        else -> "application/octet-stream"
    }
}
