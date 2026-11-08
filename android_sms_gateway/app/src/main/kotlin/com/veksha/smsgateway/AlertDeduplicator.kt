package com.veksha.smsgateway

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit

/** Prevents duplicate SMS for the same FCM event_id. Cleans up entries > 24 hours old. */
class AlertDeduplicator(context: Context) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences("veksha_dedup", Context.MODE_PRIVATE)

    companion object {
        private const val EXPIRY_MS = 24 * 60 * 60 * 1000L
    }

    fun isDuplicate(eventId: String): Boolean {
        cleanup()
        return prefs.contains(eventId)
    }

    fun markProcessed(eventId: String) {
        prefs.edit { putLong(eventId, System.currentTimeMillis()) }
    }

    private fun cleanup() {
        val now = System.currentTimeMillis()
        val toRemove = prefs.all.filter { (_, v) -> v is Long && (now - v) > EXPIRY_MS }.keys
        if (toRemove.isNotEmpty()) prefs.edit { toRemove.forEach { remove(it) } }
    }
}

