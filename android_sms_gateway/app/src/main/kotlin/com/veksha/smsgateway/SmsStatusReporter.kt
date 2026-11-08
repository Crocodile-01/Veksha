package com.veksha.smsgateway

import android.content.Context
import android.util.Log
import com.veksha.smsgateway.data.AppPreferences
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** Reports SMS delivery status back to VEKSHA backend. Runs on a background thread. */
object SmsStatusReporter {
    private const val TAG = "SmsStatusReporter"
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build()

    fun reportStatus(context: Context, eventId: String, status: String, recipient: String) {
        val prefs = AppPreferences(context)
        val backendUrl = prefs.backendUrl
        val apiKey = prefs.gatewayApiKey
        if (apiKey.isEmpty() || backendUrl.isEmpty()) {
            Log.w(TAG, "Cannot report SMS status — backend URL or API key not configured")
            return
        }
        Thread {
            try {
                val json = JSONObject().apply {
                    put("event_id", eventId)
                    put("sms_status", status)
                    put("recipient", if (recipient.length > 4) "****${recipient.takeLast(4)}" else "****")
                    put("api_key", apiKey)
                }
                val body = json.toString().toRequestBody("application/json".toMediaType())
                val response = client.newCall(
                    Request.Builder().url("$backendUrl/api/v1/gateway/sms-status").post(body).build()
                ).execute()
                Log.i(TAG, "SMS status reported: $status httpCode=${response.code}")
                response.close()
            } catch (e: Exception) {
                Log.e(TAG, "Failed to report SMS status: ${e.message}")
            }
        }.start()
    }
}

