"""
VEKSHA – Firebase Cloud Messaging (FCM) Service
================================================
Sends high-priority push notifications to registered Android SMS Gateway devices.

Design:
- Fault-tolerant: FCM failure never crashes the main request pipeline.
- Deduplication: event_id tracked in Firestore "fcm_sent_events".
- Token management: expired/invalid tokens marked inactive automatically.
- Kill-switches: FCM_ENABLED and ANDROID_GATEWAY_ENABLED env vars.

Firestore collections:
  gateway_devices/{token_hash}   – registered Android gateway devices
  fcm_sent_events/{event_id}     – deduplication tracking
  gateway_sms_status/{event_id}  – SMS delivery reports from Android app
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GATEWAY_DEVICES_COLLECTION = "gateway_devices"
FCM_SENT_EVENTS_COLLECTION = "fcm_sent_events"
SMS_STATUS_COLLECTION = "gateway_sms_status"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class FCMResult:
    success: bool
    message: str
    message_id: Optional[str] = None
    failed_tokens: List[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None  # "duplicate" | "disabled" | "no_devices"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_fcm_enabled() -> bool:
    fcm = os.getenv("FCM_ENABLED", "true").strip().lower() == "true"
    gw = os.getenv("ANDROID_GATEWAY_ENABLED", "true").strip().lower() == "true"
    return fcm and gw


def _get_db():
    """Return Firestore client via firebase_service (avoids circular import)."""
    import firebase_service  # noqa: PLC0415
    if not firebase_service._is_enabled():
        return None
    return firebase_service._db


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Gateway device registration
# ---------------------------------------------------------------------------

def register_gateway_device(token: str, device_name: str, api_key: str) -> Dict[str, Any]:
    """Register or update an Android gateway device FCM token."""
    expected_key = os.getenv("GATEWAY_API_KEY", "").strip()
    if not expected_key or api_key != expected_key:
        raise ValueError("Invalid GATEWAY_API_KEY")
    if not token or len(token) < 10:
        raise ValueError("FCM token is too short or empty")

    doc_id = _token_hash(token)
    doc_data: Dict[str, Any] = {
        "token": token,
        "device_name": device_name or "VEKSHA Gateway",
        "registered_at": time.time(),
        "last_seen": time.time(),
        "active": True,
    }
    db = _get_db()
    if db:
        try:
            db.collection(GATEWAY_DEVICES_COLLECTION).document(doc_id).set(doc_data, merge=True)
            logger.info("[FCM] Gateway device registered: %s (%s)", device_name, doc_id)
        except Exception as exc:
            logger.error("[FCM] Failed to store gateway device: %s", exc)

    return {"doc_id": doc_id, "status": "registered", **doc_data}


def unregister_gateway_device(token: str, api_key: str) -> bool:
    """Mark a gateway device inactive."""
    expected_key = os.getenv("GATEWAY_API_KEY", "").strip()
    if not expected_key or api_key != expected_key:
        raise ValueError("Invalid GATEWAY_API_KEY")

    db = _get_db()
    doc_id = _token_hash(token)
    if db:
        try:
            db.collection(GATEWAY_DEVICES_COLLECTION).document(doc_id).update({"active": False})
            logger.info("[FCM] Gateway device unregistered: %s", doc_id)
            return True
        except Exception as exc:
            logger.error("[FCM] Failed to unregister gateway device: %s", exc)
    return False


def get_registered_gateways() -> List[Dict[str, Any]]:
    """Return active registered gateway devices (tokens masked)."""
    db = _get_db()
    if not db:
        return []
    try:
        docs = db.collection(GATEWAY_DEVICES_COLLECTION).where("active", "==", True).get()
        result = []
        for doc in docs:
            d = doc.to_dict()
            d["doc_id"] = doc.id
            tok = d.pop("token", "")
            d["token_preview"] = tok[:6] + "..." + tok[-4:] if len(tok) > 10 else "***"
            result.append(d)
        return result
    except Exception as exc:
        logger.error("[FCM] Failed to fetch gateways: %s", exc)
        return []


def _get_active_tokens() -> List[str]:
    db = _get_db()
    if not db:
        return []
    try:
        docs = db.collection(GATEWAY_DEVICES_COLLECTION).where("active", "==", True).get()
        return [doc.to_dict().get("token", "") for doc in docs if doc.to_dict().get("token")]
    except Exception as exc:
        logger.error("[FCM] Failed to fetch tokens: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _is_duplicate_fcm_event(event_id: str) -> bool:
    db = _get_db()
    if not db:
        return False
    try:
        return db.collection(FCM_SENT_EVENTS_COLLECTION).document(str(event_id)).get().exists
    except Exception:
        return False


def _mark_fcm_event_sent(event_id: str, event_type: str) -> None:
    db = _get_db()
    if not db:
        return
    try:
        db.collection(FCM_SENT_EVENTS_COLLECTION).document(str(event_id)).set({
            "event_id": str(event_id),
            "event_type": event_type,
            "sent_at": time.time(),
        })
    except Exception as exc:
        logger.warning("[FCM] Failed to record sent event: %s", exc)


# ---------------------------------------------------------------------------
# FCM send
# ---------------------------------------------------------------------------

def _send_fcm_to_tokens(tokens: List[str], title: str, body: str, data: Dict[str, str]) -> FCMResult:
    if not tokens:
        return FCMResult(success=False, message="No registered gateway devices", skipped_reason="no_devices")

    try:
        from firebase_admin import messaging  # noqa: PLC0415
    except ImportError:
        return FCMResult(success=False, message="firebase_admin not installed")

    db = _get_db()
    failed: List[str] = []
    last_msg_id: Optional[str] = None

    for token in tokens:
        try:
            msg = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data=data,
                android=messaging.AndroidConfig(
                    priority="high",
                    ttl=300,
                    notification=messaging.AndroidNotification(
                        title=title,
                        body=body,
                        channel_id="veksha_alerts",
                        priority="max",
                        default_vibrate_timings=True,
                    ),
                ),
                token=token,
            )
            resp = messaging.send(msg)
            last_msg_id = resp
            logger.info("[FCM] Sent to ...%s → %s", token[-6:], resp)
            if db:
                try:
                    db.collection(GATEWAY_DEVICES_COLLECTION).document(_token_hash(token)).update({"last_seen": time.time()})
                except Exception:
                    pass
        except Exception as exc:
            err = str(exc)
            logger.error("[FCM] Failed ...%s: %s", token[-6:], err)
            failed.append(token)
            if any(x in err for x in ("registration-token-not-registered", "UNREGISTERED", "invalid-argument")):
                if db:
                    try:
                        db.collection(GATEWAY_DEVICES_COLLECTION).document(_token_hash(token)).update({"active": False})
                    except Exception:
                        pass

    success_count = len(tokens) - len(failed)
    if success_count > 0:
        return FCMResult(success=True, message=f"FCM sent to {success_count}/{len(tokens)} devices",
                         message_id=last_msg_id, failed_tokens=failed)
    return FCMResult(success=False, message=f"FCM failed for all {len(tokens)} devices", failed_tokens=failed)


# ---------------------------------------------------------------------------
# Public alert senders
# ---------------------------------------------------------------------------

def send_sos_alert_fcm(event: Dict[str, Any]) -> FCMResult:
    """Send FCM for a manual SOS event. Non-blocking, catches all exceptions."""
    if not _is_fcm_enabled():
        return FCMResult(success=False, message="FCM/Gateway disabled", skipped_reason="disabled")

    event_id = str(event.get("event_id", f"sos_{int(time.time())}"))
    if _is_duplicate_fcm_event(event_id):
        return FCMResult(success=False, message=f"Duplicate FCM event {event_id}", skipped_reason="duplicate")

    tokens = _get_active_tokens()
    hr, spo2 = event.get("heart_rate"), event.get("spo2")
    vitals = f"HR:{hr:.0f} SpO2:{spo2:.0f}%" if hr and spo2 else "Vitals unavailable"

    data = {
        "event_type": "SOS_MANUAL",
        "event_id": event_id,
        "timestamp": str(event.get("timestamp", "")),
        "heart_rate": str(hr or ""),
        "spo2": str(spo2 or ""),
        "device_id": str(event.get("device_id", "esp32-veksha")),
        "triggered_by": str(event.get("triggered_by", "manual_button")),
        "alert_reason": "Manual SOS button pressed",
    }
    result = _send_fcm_to_tokens(tokens, "🚨 VEKSHA SOS ALERT", f"Emergency SOS triggered! {vitals}", data)
    if result.success:
        _mark_fcm_event_sent(event_id, "SOS_MANUAL")
    return result


def send_abnormal_alert_fcm(alert_condition: str, alert_reason: str, vitals: Dict[str, Any], event_id: str) -> FCMResult:
    """Send FCM for a confirmed sustained abnormal reading."""
    if not _is_fcm_enabled():
        return FCMResult(success=False, message="FCM/Gateway disabled", skipped_reason="disabled")

    if _is_duplicate_fcm_event(event_id):
        return FCMResult(success=False, message=f"Duplicate FCM event {event_id}", skipped_reason="duplicate")

    tokens = _get_active_tokens()
    hr, spo2 = vitals.get("heart_rate"), vitals.get("spo2")
    vitals_str = f"HR:{hr:.0f} SpO2:{spo2:.0f}%" if hr and spo2 else "Vitals unavailable"

    data = {
        "event_type": "ABNORMAL_VITALS",
        "event_id": str(event_id),
        "timestamp": str(vitals.get("timestamp", "")),
        "heart_rate": str(hr or ""),
        "spo2": str(spo2 or ""),
        "device_id": str(vitals.get("device_id", "esp32-veksha")),
        "triggered_by": "auto_sustained_abnormal",
        "alert_reason": str(alert_reason),
        "condition": str(alert_condition),
    }
    result = _send_fcm_to_tokens(tokens, "⚠️ VEKSHA Health Alert", f"{alert_condition}: {vitals_str}", data)
    if result.success:
        _mark_fcm_event_sent(event_id, "ABNORMAL_VITALS")
    return result


# ---------------------------------------------------------------------------
# SMS status reporting (called by Android app)
# ---------------------------------------------------------------------------

def update_sms_delivery_status(event_id: str, status: str, details: Dict[str, Any], api_key: str) -> bool:
    expected_key = os.getenv("GATEWAY_API_KEY", "").strip()
    if not expected_key or api_key != expected_key:
        raise ValueError("Invalid GATEWAY_API_KEY")

    db = _get_db()
    if not db:
        logger.warning("[FCM] Cannot store SMS status — Firebase unavailable")
        return False
    try:
        db.collection(SMS_STATUS_COLLECTION).document(str(event_id)).set({
            "event_id": str(event_id),
            "sms_status": status,
            "reported_at": time.time(),
            **{k: str(v) for k, v in details.items()},
        }, merge=True)
        logger.info("[FCM] SMS status updated for event %s: %s", event_id, status)
        return True
    except Exception as exc:
        logger.error("[FCM] Failed to update SMS status: %s", exc)
        return False


# ---------------------------------------------------------------------------
# History queries
# ---------------------------------------------------------------------------

def get_recent_alert_history(limit: int = 10) -> List[Dict[str, Any]]:
    db = _get_db()
    if not db:
        return []
    try:
        docs = (db.collection(FCM_SENT_EVENTS_COLLECTION)
                .order_by("sent_at", direction="DESCENDING").limit(limit).get())
        return [{**doc.to_dict(), "doc_id": doc.id} for doc in docs]
    except Exception as exc:
        logger.error("[FCM] Failed to fetch alert history: %s", exc)
        return []


def get_recent_sms_statuses(limit: int = 10) -> List[Dict[str, Any]]:
    db = _get_db()
    if not db:
        return []
    try:
        docs = (db.collection(SMS_STATUS_COLLECTION)
                .order_by("reported_at", direction="DESCENDING").limit(limit).get())
        return [{**doc.to_dict(), "doc_id": doc.id} for doc in docs]
    except Exception as exc:
        logger.error("[FCM] Failed to fetch SMS statuses: %s", exc)
        return []

