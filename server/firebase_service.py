"""
VEKSHA - Firebase / Firestore Integration Service
==================================================
Provides a thin, fault-tolerant wrapper around the Firebase Admin SDK.

Design principles:
  - Firebase is *optional*: if the SDK is not installed, credentials are
    missing, or FIREBASE_ENABLED=false, every write silently no-ops and
    every read returns None.  The existing in-memory backend continues to
    work without any changes.
  - All Firestore I/O is encapsulated here; main.py imports only the three
    public helpers: `store_reading`, `store_sos_event`, and `firebase_status`.
  - Credentials are loaded exclusively from environment variables or a path
    referenced by an env var — never hard-coded.

Environment variables (see .env.example):
  FIREBASE_SERVICE_ACCOUNT_PATH  Path to the service-account JSON key file.
  FIREBASE_PROJECT_ID            Firebase project ID.
  FIREBASE_READINGS_COLLECTION   Firestore collection for health readings.
  FIREBASE_SOS_COLLECTION        Firestore collection for SOS events.
  FIREBASE_ENABLED               "true" to activate Firebase, anything else to skip.

Firestore document structure — health_readings/{auto-id}:
  {
    "timestamp":      "2024-01-15T10:30:00+00:00",  # ISO 8601 string
    "heart_rate":     78.5,                          # float | None
    "spo2":           98.0,                          # float | None
    "sensor_status":  "VALID_DATA",                  # string
    "device_id":      "esp32-veksha",                # string
    "server_received_at": <Firestore ServerTimestamp>
  }

Firestore document structure — sos_events/{auto-id}:
  {
    "event_id":       1,
    "timestamp":      "2024-01-15T10:30:00+00:00",
    "device_id":      "esp32-veksha",
    "triggered_by":   "manual_button",
    "alert_status":   "TRIGGERED",
    "heart_rate":     None,
    "spo2":           None,
    "sensor_status":  None,
    "gps_location":   "Not Available",
    "notes":          None,
    "sms_sent":       False,
    "whatsapp_sent":  False,
    "server_received_at": <Firestore ServerTimestamp>
  }
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

_db: Optional[Any] = None          # google.cloud.firestore.Client | None
_init_error: Optional[str] = None  # Human-readable init failure reason
_firebase_enabled: bool = False    # Mirrors FIREBASE_ENABLED env var


def reset_firebase_state() -> None:
    """Reset internal state (used during test teardown/setup)."""
    global _db, _init_error, _firebase_enabled
    _db = None
    _init_error = None
    _firebase_enabled = False


def _is_enabled() -> bool:
    """Return True when Firebase is configured and successfully initialised."""
    return _firebase_enabled and _db is not None


# ---------------------------------------------------------------------------
# Initialisation (called once at application startup)
# ---------------------------------------------------------------------------

def initialise_firebase() -> None:
    """
    Attempt to initialise the Firebase Admin SDK.

    This function is intentionally *non-fatal*: if anything goes wrong
    (missing env vars, missing credentials file, SDK not installed, network
    issues) it logs a clear warning and sets _firebase_enabled to False so
    the rest of the application continues with in-memory-only storage.
    """
    global _db, _init_error, _firebase_enabled

    # Respect the kill-switch first
    enabled_flag = os.getenv("FIREBASE_ENABLED", "true").strip().lower()
    if enabled_flag != "true":
        _init_error = "Firebase disabled via FIREBASE_ENABLED env var."
        logger.info("[Firebase] %s", _init_error)
        _firebase_enabled = False
        return

    # Validate required env vars
    sa_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    sa_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()

    parsed_json_cred = None
    if sa_json:
        try:
            parsed_json_cred = json.loads(sa_json)
        except Exception as json_err:
            _init_error = f"Invalid FIREBASE_SERVICE_ACCOUNT_JSON: {json_err}"
            logger.warning("[Firebase] %s", _init_error)
            _firebase_enabled = False
            return

    if not sa_json:
        if not sa_path:
            _init_error = (
                "Neither FIREBASE_SERVICE_ACCOUNT_JSON nor FIREBASE_SERVICE_ACCOUNT_PATH is set. "
                "Copy server/.env.example to server/.env and fill in credentials."
            )
            logger.warning("[Firebase] %s", _init_error)
            _firebase_enabled = False
            return

        if not os.path.isfile(sa_path):
            candidate = os.path.normpath(os.path.join(os.path.dirname(__file__), sa_path))
            if os.path.isfile(candidate):
                sa_path = candidate
            else:
                _init_error = (
                    f"Service account file not found at '{sa_path}'. "
                    "Download it from Firebase Console → Project Settings → Service accounts."
                )
                logger.warning("[Firebase] %s", _init_error)
                _firebase_enabled = False
                return

    if not project_id or project_id == "your-firebase-project-id-here":
        _init_error = (
            "FIREBASE_PROJECT_ID is not set or still contains the placeholder value. "
            "Set it to your real Firebase project ID in server/.env."
        )
        logger.warning("[Firebase] %s", _init_error)
        _firebase_enabled = False
        return

    # Attempt SDK import (firebase-admin may not be installed yet)
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        _init_error = (
            "firebase-admin package is not installed. "
            "Run: pip install firebase-admin"
        )
        logger.warning("[Firebase] %s", _init_error)
        _firebase_enabled = False
        return

    # Initialise the app (guard against double-init in pytest reruns)
    try:
        if not firebase_admin._apps:
            if parsed_json_cred:
                cred = credentials.Certificate(parsed_json_cred)
            else:
                cred = credentials.Certificate(sa_path)
            firebase_admin.initialize_app(cred, {"projectId": project_id})
        _db = firestore.client()
        _firebase_enabled = True
        logger.info(
            "[Firebase] Connected to project '%s'. Firestore client ready.", project_id
        )
    except Exception as exc:
        _init_error = f"Firebase initialisation failed: {exc}"
        logger.error("[Firebase] %s", _init_error)
        _firebase_enabled = False


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def _readings_collection():
    """Return the Firestore CollectionReference for health readings."""
    col = os.getenv("FIREBASE_READINGS_COLLECTION", "health_readings")
    return _db.collection(col)  # type: ignore[union-attr]


def _sos_collection():
    """Return the Firestore CollectionReference for SOS events."""
    col = os.getenv("FIREBASE_SOS_COLLECTION", "sos_events")
    return _db.collection(col)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def store_reading(reading_dict: Dict[str, Any]) -> Optional[str]:
    """
    Persist a health reading to Firestore.

    Parameters
    ----------
    reading_dict:
        The validated reading dict from main.py (already has timestamp as str).

    Returns
    -------
    str | None
        The auto-generated Firestore document ID on success, None otherwise.
    """
    if not _is_enabled():
        return None

    try:
        from firebase_admin import firestore as _fs
        doc = {
            **reading_dict,
            "server_received_at": _fs.SERVER_TIMESTAMP,
        }
        _ref = _readings_collection().add(doc)
        # add() returns (update_time, DocumentReference)
        doc_id: str = _ref[1].id
        logger.info("[Firebase] Reading stored → %s", doc_id)
        return doc_id
    except Exception as exc:
        logger.error("[Firebase] Failed to store reading: %s", exc)
        return None


def store_sos_event(event_dict: Dict[str, Any]) -> Optional[str]:
    """
    Persist an SOS alert event to Firestore.

    Parameters
    ----------
    event_dict:
        The event dict constructed in main.py's trigger_sos handler.

    Returns
    -------
    str | None
        The auto-generated Firestore document ID on success, None otherwise.
    """
    if not _is_enabled():
        return None

    try:
        from firebase_admin import firestore as _fs
        doc = {
            **event_dict,
            "server_received_at": _fs.SERVER_TIMESTAMP,
        }
        _ref = _sos_collection().add(doc)
        doc_id: str = _ref[1].id
        logger.info("[Firebase] SOS event stored → %s", doc_id)
        return doc_id
    except Exception as exc:
        logger.error("[Firebase] Failed to store SOS event: %s", exc)
        return None


def firebase_status() -> Dict[str, Any]:
    """
    Return a diagnostic status dict for the /health and /api/v1/health endpoints.

    Never raises; always returns a plain dict so the health endpoint remains
    reachable even when Firebase is fully broken.
    """
    if _firebase_enabled and _db is not None:
        return {
            "firebase_enabled": True,
            "firebase_connected": True,
            "firebase_project": os.getenv("FIREBASE_PROJECT_ID", "unknown"),
            "readings_collection": os.getenv("FIREBASE_READINGS_COLLECTION", "health_readings"),
            "sos_collection": os.getenv("FIREBASE_SOS_COLLECTION", "sos_events"),
        }
    return {
        "firebase_enabled": os.getenv("FIREBASE_ENABLED", "true").lower() == "true",
        "firebase_connected": False,
        "firebase_error": _init_error or "Not initialised",
    }


def get_latest_reading_from_firestore() -> Optional[Dict[str, Any]]:
    """
    Fetch the most recent health reading document directly from Firestore.
    Returns None if Firebase is disabled, empty, or on query exception.
    """
    if not _is_enabled():
        return None

    try:
        from firebase_admin import firestore
        col = _readings_collection()
        # Query order by server_received_at or timestamp descending
        docs = col.order_by("server_received_at", direction=firestore.Query.DESCENDING).limit(1).get()
        if not docs:
            # Fall back to timestamp order in case server_received_at is indexing
            docs = col.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1).get()

        for doc in docs:
            d = doc.to_dict()
            d["firestore_id"] = doc.id
            if "server_received_at" in d and hasattr(d["server_received_at"], "isoformat"):
                d["server_received_at"] = d["server_received_at"].isoformat()
            return d
        return None
    except Exception as exc:
        logger.warning("[Firebase] Failed to fetch latest reading: %s", exc)
        return None


def get_recent_readings_from_firestore(limit: int = 30) -> List[Dict[str, Any]]:
    """
    Fetch the most recent N health readings from Firestore for dashboard charts.
    Returns newest-first list.
    """
    if not _is_enabled():
        return []

    try:
        from firebase_admin import firestore
        col = _readings_collection()
        docs = col.order_by("server_received_at", direction=firestore.Query.DESCENDING).limit(limit).get()
        if not docs:
            docs = col.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).get()

        readings: List[Dict[str, Any]] = []
        for doc in docs:
            d = doc.to_dict()
            d["firestore_id"] = doc.id
            if "server_received_at" in d and hasattr(d["server_received_at"], "isoformat"):
                d["server_received_at"] = d["server_received_at"].isoformat()
            readings.append(d)
        return readings
    except Exception as exc:
        logger.warning("[Firebase] Failed to fetch recent readings: %s", exc)
        return []


def get_sos_history_from_firestore(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Fetch recent SOS events directly from Firestore.
    """
    if not _is_enabled():
        return []

    try:
        from firebase_admin import firestore
        col = _sos_collection()
        docs = col.order_by("server_received_at", direction=firestore.Query.DESCENDING).limit(limit).get()
        if not docs:
            docs = col.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit).get()

        events: List[Dict[str, Any]] = []
        for doc in docs:
            d = doc.to_dict()
            d["firestore_id"] = doc.id
            if "server_received_at" in d and hasattr(d["server_received_at"], "isoformat"):
                d["server_received_at"] = d["server_received_at"].isoformat()
            events.append(d)
        return events
    except Exception as exc:
        logger.warning("[Firebase] Failed to fetch SOS history from Firestore: %s", exc)
        return []
