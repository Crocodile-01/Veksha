package com.veksha.smsgateway

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.telephony.SmsManager
import android.util.Log
import com.veksha.smsgateway.data.AppPreferences
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Dispatches SMS messages through the phone's SIM using Android SmsManager. */
class SmsDispatcher(private val context: Context) {
    companion object {
        private const val TAG = "SmsDispatcher"
        private const val MAX_SMS_LENGTH = 160

        fun buildAlertMessage(eventType: String, heartRate: String, spo2: String, timestamp: String, reason: String): String {
            val label = if (eventType == "SOS_MANUAL") "EMERGENCY SOS" else "Health Alert"
            val vitals = buildString {
                if (heartRate.isNotEmpty()) append("HR:${heartRate}bpm ")
                if (spo2.isNotEmpty()) append("SpO2:${spo2}%")
            }.trim()
            return "VEKSHA $label\n${vitals.ifEmpty { "Vitals unavailable" }}\n$reason\n$timestamp"
        }
    }

    fun dispatchAlert(eventId: String, message: String): List<Pair<String, String>> {
        val prefs = AppPreferences(context)
        val contacts = prefs.getEmergencyContactList()
        if (contacts.isEmpty()) { Log.w(TAG, "No emergency contacts configured"); return emptyList() }
        val ts = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        return contacts.map { number ->
            val status = sendSms(eventId, number, message)
            prefs.appendSmsLog("[$ts] $number → $status | event:$eventId")
            Pair(number, status)
        }
    }

    private fun sendSms(eventId: String, phoneNumber: String, message: String): String {
        return try {
            val smsManager: SmsManager = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
                context.getSystemService(SmsManager::class.java)
            else @Suppress("DEPRECATION") SmsManager.getDefault()

            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_IMMUTABLE else 0

            val sentPi = PendingIntent.getBroadcast(context, phoneNumber.hashCode(),
                Intent(SmsSentReceiver.ACTION).apply {
                    putExtra(SmsSentReceiver.EXTRA_EVENT_ID, eventId)
                    putExtra(SmsSentReceiver.EXTRA_RECIPIENT, phoneNumber)
                    setPackage(context.packageName)
                }, flags)
            val deliveryPi = PendingIntent.getBroadcast(context, phoneNumber.hashCode() + 1,
                Intent(SmsDeliveryReceiver.ACTION).apply {
                    putExtra(SmsDeliveryReceiver.EXTRA_EVENT_ID, eventId)
                    putExtra(SmsDeliveryReceiver.EXTRA_RECIPIENT, phoneNumber)
                    setPackage(context.packageName)
                }, flags)

            val finalMsg = if (message.length > MAX_SMS_LENGTH) message.take(MAX_SMS_LENGTH - 3) + "..." else message
            smsManager.sendTextMessage(phoneNumber, null, finalMsg, sentPi, deliveryPi)
            Log.i(TAG, "SMS queued to $phoneNumber for event $eventId")
            "QUEUED"
        } catch (e: Exception) {
            Log.e(TAG, "SMS dispatch failed for $phoneNumber: ${e.message}")
            "FAILED: ${e.message?.take(80)}"
        }
    }
}

