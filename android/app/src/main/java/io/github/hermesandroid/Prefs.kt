package io.github.hermesandroid

import android.content.Context

object Prefs {
    private const val FILE = "hermes"
    private const val DEFAULT_PORT = 9119

    private fun prefs(context: Context) = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun port(context: Context): Int = prefs(context).getInt("port", DEFAULT_PORT)

    fun setPort(context: Context, port: Int) {
        prefs(context).edit().putInt("port", port).apply()
    }

    fun keepAwake(context: Context): Boolean = prefs(context).getBoolean("keep_awake", true)

    fun setKeepAwake(context: Context, value: Boolean) {
        prefs(context).edit().putBoolean("keep_awake", value).apply()
    }

    fun autoStart(context: Context): Boolean = prefs(context).getBoolean("auto_start", true)

    fun setAutoStart(context: Context, value: Boolean) {
        prefs(context).edit().putBoolean("auto_start", value).apply()
    }
}
