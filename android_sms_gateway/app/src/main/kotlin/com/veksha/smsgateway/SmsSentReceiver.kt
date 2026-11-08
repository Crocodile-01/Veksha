package com.veksha.smsgateway

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.SmsManager
import android.util.Log

class SmsSentReceiver : BroadcastReceiver() {
    companion object {
        const val ACTION = "com.veksha.smsgateway.SMS_SENT"
        const val EXTRA_EVENT_ID = "event_id"
        const val EXTRA_RECIPIENT = "recipient"
        private const val TAG = "SmsSentReceiver"
    }

    override fun onReceive(context: Context, intent: Intent) {
        val eventId = intent.getStringExtra(EXTRA_EVENT_ID) ?: "unknown"
        val recipient = intent.getStringExtra(EXTRA_RECIPIENT) ?: "unknown"
        val status = when (resultCode) {
            Activity.RESULT_OK -> { Log.i(TAG, "SMS sent to telephony: event=$eventId"); "SENT_TO_TELEPHONY" }
            SmsManager.RESULT_ERROR_GENERIC_FAILURE -> { Log.e(TAG, "SMS generic failure: event=$eventId"); "FAILED" }
            SmsManager.RESULT_ERROR_NO_SERVICE -> { Log.e(TAG, "SMS no service: event=$eventId"); "FAILED" }
            SmsManager.RESULT_ERROR_RADIO_OFF -> { Log.e(TAG, "SMS radio off: event=$eventId"); "FAILED" }
            else -> { Log.w(TAG, "SMS result=$resultCode: event=$eventId"); "FAILED" }
        }
        SmsStatusReporter.reportStatus(context, eventId, status, recipient)
    }
}

