package com.veksha.smsgateway.data

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit

class AppPreferences(context: Context) {
    private val prefs: SharedPreferences =
        context.getSharedPreferences("veksha_gateway_prefs", Context.MODE_PRIVATE)

    companion object {
        private const val KEY_BACKEND_URL = "backend_url"
        private const val KEY_API_KEY = "gateway_api_key"
        private const val KEY_CONTACTS = "emergency_contacts"
        private const val KEY_AUTO_SMS = "auto_sms_enabled"
        private const val KEY_FCM_TOKEN = "fcm_token"
        private const val KEY_REGISTERED = "gateway_registered"
        private const val KEY_LAST_ALERT = "last_alert_time"
        private const val KEY_SMS_LOG = "sms_log"
        const val DEFAULT_BACKEND_URL = "http://192.168.1.100:8000"
    }

    var backendUrl: String
        get() = prefs.getString(KEY_BACKEND_URL, DEFAULT_BACKEND_URL) ?: DEFAULT_BACKEND_URL
        set(v) = prefs.edit { putString(KEY_BACKEND_URL, v.trimEnd('/')) }

    var gatewayApiKey: String
        get() = prefs.getString(KEY_API_KEY, "") ?: ""
        set(v) = prefs.edit { putString(KEY_API_KEY, v) }

    var emergencyContacts: String
        get() = prefs.getString(KEY_CONTACTS, "") ?: ""
        set(v) = prefs.edit { putString(KEY_CONTACTS, v) }

    fun getEmergencyContactList(): List<String> =
        emergencyContacts.split(",").map { it.trim() }.filter { it.isNotEmpty() }

    var autoSmsEnabled: Boolean
        get() = prefs.getBoolean(KEY_AUTO_SMS, false)
        set(v) = prefs.edit { putBoolean(KEY_AUTO_SMS, v) }

    var fcmToken: String
        get() = prefs.getString(KEY_FCM_TOKEN, "") ?: ""
        set(v) = prefs.edit { putString(KEY_FCM_TOKEN, v) }

    var gatewayRegistered: Boolean
        get() = prefs.getBoolean(KEY_REGISTERED, false)
        set(v) = prefs.edit { putBoolean(KEY_REGISTERED, v) }

    var lastAlertTime: Long
        get() = prefs.getLong(KEY_LAST_ALERT, 0L)
        set(v) = prefs.edit { putLong(KEY_LAST_ALERT, v) }

    fun appendSmsLog(entry: String) {
        val existing = prefs.getString(KEY_SMS_LOG, "") ?: ""
        val lines = existing.lines().takeLast(49).toMutableList()
        lines.add(entry)
        prefs.edit { putString(KEY_SMS_LOG, lines.joinToString("\n")) }
    }

    fun getSmsLog(): List<String> =
        (prefs.getString(KEY_SMS_LOG, "") ?: "").lines().filter { it.isNotEmpty() }.reversed()

    fun clearSmsLog() = prefs.edit { remove(KEY_SMS_LOG) }
}

