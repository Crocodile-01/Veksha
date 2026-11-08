package com.veksha.smsgateway

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class SmsDeliveryReceiver : BroadcastReceiver() {
    companion object {
        const val ACTION = "com.veksha.smsgateway.SMS_DELIVERED"
        const val EXTRA_EVENT_ID = "event_id"
        const val EXTRA_RECIPIENT = "recipient"
        private const val TAG = "SmsDeliveryReceiver"
    }

    override fun onReceive(context: Context, intent: Intent) {
        val eventId = intent.getStringExtra(EXTRA_EVENT_ID) ?: "unknown"
        val recipient = intent.getStringExtra(EXTRA_RECIPIENT) ?: "unknown"
        val status = when (resultCode) {
            android.app.Activity.RESULT_OK -> { Log.i(TAG, "SMS delivery confirmed: event=$eventId"); "DELIVERY_CONFIRMED" }
            else -> { Log.w(TAG, "SMS delivery unconfirmed (code=$resultCode): event=$eventId"); "SENT_TO_TELEPHONY" }
        }
        SmsStatusReporter.reportStatus(context, eventId, status, recipient)
    }
}

