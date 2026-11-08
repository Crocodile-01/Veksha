package com.veksha.smsgateway

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.util.Log
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.veksha.smsgateway.data.AppPreferences

class VekshaFirebaseMessagingService : FirebaseMessagingService() {
    companion object {
        private const val TAG = "VekshaFCMService"
        const val CHANNEL_ID = "veksha_alerts"
    }

    private val deduplicator by lazy { AlertDeduplicator(this) }
    private val smsDispatcher by lazy { SmsDispatcher(this) }

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        Log.i(TAG, "FCM token refreshed: ...${token.takeLast(6)}")
        AppPreferences(this).also { it.fcmToken = token; it.gatewayRegistered = false }
        Thread { GatewayRegistrationManager(this).register() }.start()
    }

    override fun onMessageReceived(remoteMessage: RemoteMessage) {
        super.onMessageReceived(remoteMessage)
        val data = remoteMessage.data
        val eventType = data["event_type"] ?: return
        val eventId = data["event_id"] ?: return
        Log.i(TAG, "FCM received: type=$eventType id=$eventId")

        if (deduplicator.isDuplicate(eventId)) { Log.i(TAG, "Duplicate ignored: $eventId"); return }
        deduplicator.markProcessed(eventId)

        val heartRate = data["heart_rate"] ?: ""
        val spo2 = data["spo2"] ?: ""
        val timestamp = data["timestamp"] ?: java.time.Instant.now().toString()
        val reason = data["alert_reason"] ?: ""
        val condition = data["condition"] ?: ""

        val alertMessage = SmsDispatcher.buildAlertMessage(
            eventType, heartRate, spo2, timestamp,
            if (condition.isNotEmpty()) "$condition - $reason" else reason
        )

        showNotification(eventType, alertMessage, eventId)
        AppPreferences(this).lastAlertTime = System.currentTimeMillis()

        val prefs = AppPreferences(this)
        if (prefs.autoSmsEnabled && hasSmsPermission()) {
            smsDispatcher.dispatchAlert(eventId, alertMessage).forEach { (recipient, status) ->
                SmsStatusReporter.reportStatus(this, eventId, status, recipient)
            }
        } else {
            Log.w(TAG, "SMS skipped: autoSms=${prefs.autoSmsEnabled} perm=${hasSmsPermission()}")
        }
    }

    private fun hasSmsPermission() =
        ActivityCompat.checkSelfPermission(this, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED

    private fun showNotification(eventType: String, message: String, eventId: String) {
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(NotificationChannel(CHANNEL_ID, "VEKSHA Emergency Alerts",
                NotificationManager.IMPORTANCE_HIGH).apply {
                enableVibration(true); vibrationPattern = longArrayOf(0, 500, 200, 500); setBypassDnd(true)
            })
        }
        val title = if (eventType == "SOS_MANUAL") "🚨 VEKSHA SOS ALERT" else "⚠️ VEKSHA Health Alert"
        val notif = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(message.take(100))
            .setStyle(NotificationCompat.BigTextStyle().bigText(message))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setContentIntent(PendingIntent.getActivity(this, 0,
                Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            .setAutoCancel(true).build()

        if (ActivityCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)
            NotificationManagerCompat.from(this).notify(eventId.hashCode(), notif)
    }
}

