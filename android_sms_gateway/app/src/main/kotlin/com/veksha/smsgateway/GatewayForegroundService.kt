package com.veksha.smsgateway

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.IBinder
import androidx.core.app.NotificationCompat

/** Foreground service to keep FCM delivery reliable on aggressive battery managers (OnePlus). */
class GatewayForegroundService : Service() {
    companion object {
        const val CHANNEL_ID = "veksha_gateway_service"
        const val NOTIFICATION_ID = 1001
        const val ACTION_START = "START_GATEWAY"
        const val ACTION_STOP = "STOP_GATEWAY"
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { stopForeground(STOP_FOREGROUND_REMOVE); stopSelf() }
        else startForeground(NOTIFICATION_ID, buildNotification())
        return START_STICKY
    }

    private fun buildNotification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            manager.createNotificationChannel(NotificationChannel(CHANNEL_ID, "VEKSHA Gateway Service",
                NotificationManager.IMPORTANCE_LOW).apply {
                description = "Keeps VEKSHA SMS gateway active in background"
            })
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("VEKSHA SMS Gateway Active")
            .setContentText("Listening for emergency health alerts")
            .setPriority(NotificationCompat.PRIORITY_LOW).setOngoing(true).build()
    }
}

