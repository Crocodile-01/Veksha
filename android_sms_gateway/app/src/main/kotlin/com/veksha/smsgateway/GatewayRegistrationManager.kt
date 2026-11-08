package com.veksha.smsgateway

import android.content.Context
import android.util.Log
import com.veksha.smsgateway.data.AppPreferences
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class GatewayRegistrationManager(private val context: Context) {
    companion object { private const val TAG = "GatewayRegistration" }
    private val prefs = AppPreferences(context)
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(15, TimeUnit.SECONDS).build()

    data class RegistrationResult(val success: Boolean, val message: String)

    fun register(deviceName: String = android.os.Build.MODEL): RegistrationResult {
        val token = prefs.fcmToken
        val apiKey = prefs.gatewayApiKey
        val backendUrl = prefs.backendUrl
        if (token.isEmpty()) return RegistrationResult(false, "FCM token not available yet")
        if (apiKey.isEmpty()) return RegistrationResult(false, "GATEWAY_API_KEY not set in app settings")
        if (backendUrl.isEmpty()) return RegistrationResult(false, "Backend URL not configured")
        return try {
            val body = JSONObject().apply {
                put("token", token); put("device_name", deviceName); put("api_key", apiKey)
            }.toString().toRequestBody("application/json".toMediaType())
            val response = client.newCall(
                Request.Builder().url("$backendUrl/api/v1/gateway/register").post(body).build()
            ).execute()
            val ok = response.isSuccessful
            response.close()
            prefs.gatewayRegistered = ok
            if (ok) RegistrationResult(true, "Registered as SMS Gateway")
            else RegistrationResult(false, "Registration failed: HTTP ${response.code}")
        } catch (e: Exception) {
            prefs.gatewayRegistered = false
            RegistrationResult(false, "Network error: ${e.message}")
        }
    }

    fun unregister(): RegistrationResult {
        val token = prefs.fcmToken; val apiKey = prefs.gatewayApiKey; val backendUrl = prefs.backendUrl
        if (token.isEmpty() || apiKey.isEmpty() || backendUrl.isEmpty()) {
            prefs.gatewayRegistered = false
            return RegistrationResult(true, "Cleared local registration")
        }
        return try {
            val url = "$backendUrl/api/v1/gateway/unregister?token=${URLEncoder.encode(token, "UTF-8")}&api_key=${URLEncoder.encode(apiKey, "UTF-8")}"
            val response = client.newCall(Request.Builder().url(url).delete().build()).execute()
            prefs.gatewayRegistered = false
            RegistrationResult(response.isSuccessful, if (response.isSuccessful) "Unregistered" else "HTTP ${response.code}")
        } catch (e: Exception) {
            prefs.gatewayRegistered = false
            RegistrationResult(false, "Network error: ${e.message}")
        }
    }
}

