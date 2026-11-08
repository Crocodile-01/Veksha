"""
VEKSHA - Emergency Health Monitoring & Rescue Alert System
FastAPI Backend Service (v1.2.0)

Endpoints:
- GET  /health                     : System health check (includes Firebase & SMS status)
- GET  /api/v1/health              : API versioned health check
- POST /api/v1/readings            : Ingest sensor telemetry, evaluate sustained anomalies, persist to Firestore
- GET  /api/v1/readings/latest     : Retrieve latest reading (Firestore or in-memory)
- GET  /api/v1/readings/history    : Retrieve recent readings for telemetry trend charts
- POST /api/v1/sos                 : Trigger emergency SOS alert, persist to Firestore, dispatch SMS to contacts
- GET  /api/v1/sos/history         : Retrieve SOS alert history (Firestore or in-memory)
- GET  /api/v1/config/thresholds   : Retrieve active anomaly detection thresholds and SMS status

Disclaimer:
Educational student prototype. Not intended for clinical or emergency diagnosis.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Load .env before importing dependent modules
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.isfile(_env_path):
        load_dotenv(_env_path)
        logging.getLogger(__name__).info("Loaded environment from %s", _env_path)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Service Dependencies
# ---------------------------------------------------------------------------
import firebase_service
import fcm_service
from abnormal_detector import global_detector, ThresholdConfig
from sms_service import (
    dispatch_emergency_sms,
    is_sms_enabled,
    get_emergency_contacts,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# Initialise Firebase at startup (fault-tolerant)
firebase_service.initialise_firebase()


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class SensorStatus(str, Enum):
    VALID_DATA = "VALID_DATA"
    NO_FINGER = "NO_FINGER"
    INVALID_DATA = "INVALID_DATA"
    CALIBRATING = "CALIBRATING"


class HealthReadingInput(BaseModel):
    timestamp: Optional[datetime] = Field(
        default=None,
        description="ISO 8601 timestamp. Defaults to current server UTC time if omitted.",
    )
    heart_rate: Optional[float] = Field(
        default=None,
        description="Estimated heart rate in beats per minute (BPM).",
    )
    spo2: Optional[float] = Field(
        default=None,
        description="Estimated blood oxygen saturation percentage (SpO2 %).",
    )
    sensor_status: SensorStatus = Field(
        description="Operational status reported by sensor (VALID_DATA, NO_FINGER, INVALID_DATA, CALIBRATING).",
    )
    device_id: Optional[str] = Field(
        default="esp32-veksha",
        description="Identifier of publishing hardware node.",
    )

    @model_validator(mode="after")
    def validate_vitals(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)

        if self.sensor_status == SensorStatus.VALID_DATA:
            if self.heart_rate is None or self.spo2 is None:
                raise ValueError("Both 'heart_rate' and 'spo2' are required when sensor_status is 'VALID_DATA'.")
            if not (30.0 <= self.heart_rate <= 240.0):
                raise ValueError(f"Heart rate {self.heart_rate} BPM is outside plausible limits (30 - 240 BPM).")
            if not (50.0 <= self.spo2 <= 100.0):
                raise ValueError(f"SpO2 {self.spo2}% is outside plausible limits (50% - 100%).")
        elif self.sensor_status in (SensorStatus.NO_FINGER, SensorStatus.INVALID_DATA, SensorStatus.CALIBRATING):
            if self.heart_rate is not None and self.heart_rate < 0:
                raise ValueError("Heart rate cannot be negative.")
            if self.spo2 is not None and self.spo2 < 0:
                raise ValueError("SpO2 cannot be negative.")

        return self


class HealthReadingResponse(BaseModel):
    status: str
    message: str
    data: Dict[str, Any]


class SOSAlertStatus(str, Enum):
    TRIGGERED = "TRIGGERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class SOSRequest(BaseModel):
    device_id: Optional[str] = Field(
        default="esp32-veksha",
        description="Identifier of hardware node associated with alert.",
    )
    triggered_by: Optional[str] = Field(
        default="manual_button",
        description="Origin of trigger (e.g. manual_button, auto_sustained_abnormal).",
    )
    heart_rate: Optional[float] = Field(default=None, description="HR at time of SOS (BPM).")
    spo2: Optional[float] = Field(default=None, description="SpO2 at time of SOS (%).")
    sensor_status: Optional[str] = Field(default=None, description="Sensor status at trigger time.")
    gps_location: Optional[str] = Field(
        default="Not Available",
        description="GPS coordinates placeholder.",
    )
    notes: Optional[str] = Field(default=None, description="Optional note or clinical description.")


class SOSResponse(BaseModel):
    status: str
    message: str
    data: Dict[str, Any]


# ---------------------------------------------------------------------------
# In-memory storage & state
# ---------------------------------------------------------------------------

latest_reading: Optional[Dict[str, Any]] = None
recent_readings: List[Dict[str, Any]] = []
MAX_HISTORY_LENGTH = 100

sos_events: List[Dict[str, Any]] = []
MAX_SOS_HISTORY = 50
_last_manual_sos_timestamp: float = 0.0

app = FastAPI(
    title="VEKSHA Health Monitoring API",
    description="Backend API to ingest, validate, detect sustained anomalies, and dispatch emergency alerts.",
    version="1.2.0",
)

# Configurable CORS origins for production deployment
cors_origins_env = os.getenv("CORS_ORIGINS", "*").strip()
if cors_origins_env == "*":
    allowed_origins = ["*"]
else:
    allowed_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health Check Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"])
@app.get("/api/v1/health", tags=["Health"])
def health_check():
    """Verify backend availability, Firebase connectivity, and SMS status."""
    return {
        "status": "online",
        "service": "VEKSHA Emergency Health Monitoring Backend",
        "version": "1.2.0",
        "server_time": datetime.now(timezone.utc).isoformat(),
        "firebase": firebase_service.firebase_status(),
        "sms": {
            "enabled": is_sms_enabled(),
            "provider": os.getenv("SMS_PROVIDER", "mock"),
            "contacts_configured": len(get_emergency_contacts()),
        },
    }


@app.get("/api/v1/config/thresholds", tags=["Config"])
def get_threshold_config():
    """Return active clinical threshold and cooldown settings."""
    return {
        "status": "success",
        "thresholds": {
            "hr_min": ThresholdConfig.get_hr_min(),
            "hr_max": ThresholdConfig.get_hr_max(),
            "spo2_min": ThresholdConfig.get_spo2_min(),
            "sustained_count": ThresholdConfig.get_sustained_count(),
            "cooldown_seconds": ThresholdConfig.get_cooldown_seconds(),
        },
        "sms_enabled": is_sms_enabled(),
        "contacts_count": len(get_emergency_contacts()),
    }


# ---------------------------------------------------------------------------
# Reading Endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/readings",
    response_model=HealthReadingResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Readings"],
)
def submit_reading(reading: HealthReadingInput):
    """
    Ingest a new health reading from ESP32 node.
    - Validates physiological constraints
    - Stores in memory & persists to Firestore
    - Evaluates sustained abnormal vitals (Task 2)
    - Dispatches automatic emergency alert & SMS when sustained abnormality confirmed (Task 2 & 5)
    """
    global latest_reading

    reading_dict = reading.model_dump()
    reading_dict["timestamp"] = reading.timestamp.isoformat()

    # --- In-memory store ---
    latest_reading = reading_dict
    recent_readings.append(reading_dict)
    if len(recent_readings) > MAX_HISTORY_LENGTH:
        recent_readings.pop(0)

    # --- Firestore persist ---
    firestore_id = firebase_service.store_reading(reading_dict)

    response_data = {**reading_dict}
    if firestore_id:
        response_data["firestore_id"] = firestore_id

    # --- Task 2: Evaluate Sustained Abnormal Reading ---
    abnormal_alert = global_detector.evaluate_reading(reading_dict)
    if abnormal_alert:
        logger.warning(
            "[ALERT ENGINE] Sustained abnormality confirmed: %s (HR=%.1f, SpO2=%.1f)",
            abnormal_alert.condition, abnormal_alert.heart_rate or 0, abnormal_alert.spo2 or 0
        )

        # Dispatch emergency SMS notification
        sms_res = dispatch_emergency_sms(
            event_type="SUSTAINED_ABNORMAL",
            vitals=reading_dict,
            reason=abnormal_alert.reason,
            notes=f"Automatic alert: {abnormal_alert.condition}",
        )

        # Record auto-generated emergency SOS event in memory and Firestore
        auto_event: Dict[str, Any] = {
            "event_id": len(sos_events) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device_id": reading.device_id,
            "triggered_by": "auto_sustained_abnormal",
            "alert_status": SOSAlertStatus.TRIGGERED.value,
            "heart_rate": reading.heart_rate,
            "spo2": reading.spo2,
            "sensor_status": reading.sensor_status.value,
            "gps_location": "Not Available",
            "notes": abnormal_alert.reason,
            "sms_sent": sms_res.success,
            "sms_status": sms_res.message,
            "whatsapp_sent": False,
        }
        sos_events.append(auto_event)
        if len(sos_events) > MAX_SOS_HISTORY:
            sos_events.pop(0)

        auto_doc_id = firebase_service.store_sos_event(auto_event)
        if auto_doc_id:
            auto_event["firestore_id"] = auto_doc_id

        # --- FCM: notify Android SMS Gateway (non-fatal) ---
        try:
            _fcm_event_id = f"abnormal_{auto_event['event_id']}_{int(time.time())}"
            fcm_res = fcm_service.send_abnormal_alert_fcm(
                alert_condition=abnormal_alert.condition,
                alert_reason=abnormal_alert.reason,
                vitals=reading_dict,
                event_id=_fcm_event_id,
            )
            logger.info("[FCM] Abnormal alert FCM result: %s", fcm_res.message)
        except Exception as _fcm_exc:
            logger.warning("[FCM] Abnormal FCM dispatch failed (non-fatal): %s", _fcm_exc)

        response_data["auto_alert"] = {
            "condition": abnormal_alert.condition,
            "reason": abnormal_alert.reason,
            "sms_sent": sms_res.success,
            "sms_message": sms_res.message,
        }

    return HealthReadingResponse(
        status="success",
        message="Reading validated and stored successfully.",
        data=response_data,
    )


@app.get("/api/v1/readings/latest", tags=["Readings"])
def get_latest_reading(source: str = Query("auto", description="Data source: 'auto', 'memory', or 'firebase'")):
    """
    Return the most recently received telemetry frame.
    When source='firebase', queries Firestore directly.
    """
    selected_reading = None
    source_used = "memory"

    if source.lower() == "firebase" and firebase_service._is_enabled():
        selected_reading = firebase_service.get_latest_reading_from_firestore()
        source_used = "firebase"
    elif source.lower() == "auto":
        # Prefer memory if present, fallback to Firestore if memory is empty
        if latest_reading is not None:
            selected_reading = latest_reading
            source_used = "memory"
        elif firebase_service._is_enabled():
            selected_reading = firebase_service.get_latest_reading_from_firestore()
            if selected_reading:
                source_used = "firebase"

    if selected_reading is None:
        return {
            "status": "no_data",
            "message": "No sensor readings have been received yet.",
            "data": None,
        }

    return {
        "status": "success",
        "source": source_used,
        "data": selected_reading,
    }


@app.get("/api/v1/readings/history", tags=["Readings"])
def get_readings_history(limit: int = Query(30, ge=1, le=100)):
    """
    Return recent telemetry readings for chart rendering.
    Queries Firestore if available, otherwise returns in-memory history.
    """
    if firebase_service._is_enabled():
        firestore_data = firebase_service.get_recent_readings_from_firestore(limit=limit)
        if firestore_data:
            return {
                "status": "success",
                "source": "firebase",
                "total": len(firestore_data),
                "readings": list(reversed(firestore_data)),
            }

    # Fallback to in-memory
    mem_subset = recent_readings[-limit:] if len(recent_readings) > limit else list(recent_readings)
    return {
        "status": "success",
        "source": "memory",
        "total": len(mem_subset),
        "readings": mem_subset,
    }


# ---------------------------------------------------------------------------
# SOS Endpoints (Task 5 & 6)
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/sos",
    response_model=SOSResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["SOS"],
)
def trigger_sos(sos: SOSRequest):
    """
    Record an SOS alert event triggered by the patient/user.
    - Rate-limiting cooldown prevents duplicate/spam clicks
    - Persists event to Firestore
    - Dispatches emergency SMS to configured emergency contacts
    """
    global _last_manual_sos_timestamp

    sos_cooldown_window = int(os.getenv("SOS_COOLDOWN_SECONDS", "15"))
    now = time.time()
    elapsed = now - _last_manual_sos_timestamp

    if elapsed < sos_cooldown_window:
        remaining = int(sos_cooldown_window - elapsed)
        logger.warning("[SOS] Manual SOS rejected: in cooldown (%ds remaining)", remaining)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"SOS alert rate limit active. Please wait {remaining} second(s) before sending another alert.",
        )

    _last_manual_sos_timestamp = now

    event: Dict[str, Any] = {
        "event_id": len(sos_events) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "device_id": sos.device_id,
        "triggered_by": sos.triggered_by or "manual_button",
        "alert_status": SOSAlertStatus.TRIGGERED.value,
        "heart_rate": sos.heart_rate,
        "spo2": sos.spo2,
        "sensor_status": sos.sensor_status,
        "gps_location": sos.gps_location or "Not Available",
        "notes": sos.notes,
        "sms_sent": False,
        "sms_status": "PENDING",
        "whatsapp_sent": False,
    }

    # Dispatch SMS to emergency contacts (Task 5)
    sms_res = dispatch_emergency_sms(
        event_type="SOS_MANUAL_ALERT",
        vitals=event,
        reason=sos.notes or "Manual SOS button pressed on VEKSHA dashboard",
        notes=sos.notes,
    )
    event["sms_sent"] = sms_res.success
    event["sms_status"] = sms_res.message
    if sms_res.recipients_sent:
        event["sms_recipients"] = sms_res.recipients_sent

    # --- In-memory store ---
    sos_events.append(event)
    if len(sos_events) > MAX_SOS_HISTORY:
        sos_events.pop(0)

    # --- Firestore persist ---
    firestore_id = firebase_service.store_sos_event(event)

    # --- FCM: notify Android SMS Gateway (non-fatal) ---
    try:
        fcm_res = fcm_service.send_sos_alert_fcm(event)
        event["fcm_sent"] = fcm_res.success
        event["fcm_status"] = fcm_res.message
        logger.info("[FCM] SOS FCM result: %s", fcm_res.message)
    except Exception as _fcm_exc:
        logger.warning("[FCM] SOS FCM dispatch failed (non-fatal): %s", _fcm_exc)

    response_data = {**event}
    if firestore_id:
        response_data["firestore_id"] = firestore_id

    return SOSResponse(
        status="sos_triggered",
        message=f"SOS alert recorded successfully. SMS status: {sms_res.message}",
        data=response_data,
    )


@app.get("/api/v1/sos/history", tags=["SOS"])
def get_sos_history(source: str = Query("auto", description="'auto', 'firebase', or 'memory'")):
    """
    Return full SOS alert history (most recent first).
    """
    if source.lower() in ("firebase", "auto") and firebase_service._is_enabled():
        fs_events = firebase_service.get_sos_history_from_firestore(limit=25)
        if fs_events:
            return {
                "status": "success",
                "source": "firebase",
                "total": len(fs_events),
                "events": fs_events,
            }

    return {
        "status": "success",
        "source": "memory",
        "total": len(sos_events),
        "events": list(reversed(sos_events)),
    }


# ---------------------------------------------------------------------------
# Android SMS Gateway Endpoints
# ---------------------------------------------------------------------------

class GatewayRegisterRequest(BaseModel):
    token: str = Field(..., description="Firebase Cloud Messaging device token.")
    device_name: Optional[str] = Field(default="VEKSHA Gateway", description="Human-readable device name.")
    api_key: str = Field(..., description="GATEWAY_API_KEY from server .env.")


class SMSStatusReportRequest(BaseModel):
    event_id: str = Field(..., description="Event ID the SMS was sent for.")
    sms_status: str = Field(..., description="QUEUED | SENT_TO_TELEPHONY | DELIVERY_CONFIRMED | FAILED")
    recipient: Optional[str] = Field(default=None, description="Recipient phone, last 4 digits only.")
    error_message: Optional[str] = Field(default=None, description="Error detail if status is FAILED.")
    api_key: str = Field(..., description="GATEWAY_API_KEY from server .env.")


@app.post("/api/v1/gateway/register", tags=["Gateway"], status_code=status.HTTP_201_CREATED)
def register_gateway(req: GatewayRegisterRequest):
    """Register or update an Android SMS Gateway device FCM token."""
    try:
        result = fcm_service.register_gateway_device(
            token=req.token,
            device_name=req.device_name or "VEKSHA Gateway",
            api_key=req.api_key,
        )
        return {"status": "registered", "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    except Exception as exc:
        logger.error("[Gateway] Registration error: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Registration failed")


@app.delete("/api/v1/gateway/unregister", tags=["Gateway"])
def unregister_gateway(token: str, api_key: str):
    """Remove a gateway device registration."""
    try:
        success = fcm_service.unregister_gateway_device(token=token, api_key=api_key)
        return {"status": "unregistered" if success else "not_found"}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


@app.get("/api/v1/gateway/status", tags=["Gateway"])
def get_gateway_status():
    """Return registered gateway devices and recent SMS delivery status."""
    return {
        "status": "success",
        "fcm_enabled": fcm_service._is_fcm_enabled(),
        "gateway_enabled": os.getenv("ANDROID_GATEWAY_ENABLED", "true").lower() == "true",
        "gateway_count": len(fcm_service.get_registered_gateways()),
        "gateways": fcm_service.get_registered_gateways(),
        "recent_sms_statuses": fcm_service.get_recent_sms_statuses(limit=5),
        "recent_fcm_alerts": fcm_service.get_recent_alert_history(limit=5),
    }


@app.post("/api/v1/gateway/sms-status", tags=["Gateway"])
def report_sms_status(req: SMSStatusReportRequest):
    """Android gateway app reports SMS delivery result."""
    valid_statuses = {"QUEUED", "SENT_TO_TELEPHONY", "DELIVERY_CONFIRMED", "FAILED"}
    if req.sms_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid sms_status. Must be one of: {valid_statuses}",
        )
    try:
        details: Dict[str, Any] = {}
        if req.recipient:
            details["recipient"] = req.recipient
        if req.error_message:
            details["error_message"] = req.error_message
        success = fcm_service.update_sms_delivery_status(
            event_id=req.event_id, status=req.sms_status, details=details, api_key=req.api_key,
        )
        return {"status": "recorded" if success else "partial", "event_id": req.event_id, "sms_status": req.sms_status}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    except Exception as exc:
        logger.error("[Gateway] SMS status report error: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to record status")


@app.get("/api/v1/gateway/alerts", tags=["Gateway"])
def get_gateway_alert_history(limit: int = Query(10, ge=1, le=50)):
    """Return recent FCM alert history sent to gateway devices."""
    history = fcm_service.get_recent_alert_history(limit=limit)
    return {"status": "success", "total": len(history), "alerts": history}


# ---------------------------------------------------------------------------
# Mount dashboard static files at root AFTER all API routes
# ---------------------------------------------------------------------------
dashboard_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dashboard"))
if os.path.exists(dashboard_path):
    app.mount("/", StaticFiles(directory=dashboard_path, html=True), name="dashboard")
