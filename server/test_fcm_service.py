"""
Tests for fcm_service.py — VEKSHA Android SMS Gateway FCM Service.
All Firebase and messaging calls are mocked.
env vars are patched at call-time via patch.dict on each test.
"""
from __future__ import annotations

import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

_REAL_MODULES: dict = {}


@pytest.fixture(autouse=True, scope="module")
def isolate_firebase_modules():
    """Save real modules, inject mocks, restore after module-level tests finish."""
    for name in ("firebase_service", "firebase_admin", "firebase_admin.messaging", "fcm_service"):
        _REAL_MODULES[name] = sys.modules.pop(name, None)

    fs_mock = types.ModuleType("firebase_service")
    fs_mock._is_enabled = MagicMock(return_value=False)
    fs_mock._db = None
    sys.modules["firebase_service"] = fs_mock

    fa_mock = types.ModuleType("firebase_admin")
    fa_mock.messaging = MagicMock()
    sys.modules["firebase_admin"] = fa_mock
    sys.modules["firebase_admin.messaging"] = fa_mock.messaging

    import fcm_service  # noqa: PLC0415
    sys.modules["fcm_service"] = fcm_service

    yield

    # Restore originals so other test modules get the real firebase_service
    for name, mod in _REAL_MODULES.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def _svc():
    return sys.modules["fcm_service"]


def test_fcm_disabled_returns_clean_result():
    with patch.dict(os.environ, {"FCM_ENABLED": "false", "ANDROID_GATEWAY_ENABLED": "true"}):
        result = _svc().send_sos_alert_fcm({"event_id": "1", "heart_rate": 75, "spo2": 98})
    assert result.success is False
    assert result.skipped_reason == "disabled"


def test_gateway_disabled_returns_clean_result():
    with patch.dict(os.environ, {"FCM_ENABLED": "true", "ANDROID_GATEWAY_ENABLED": "false"}):
        result = _svc().send_sos_alert_fcm({"event_id": "2"})
    assert result.success is False
    assert result.skipped_reason == "disabled"


def test_send_fcm_no_registered_devices():
    with patch.dict(os.environ, {"FCM_ENABLED": "true", "ANDROID_GATEWAY_ENABLED": "true"}):
        result = _svc().send_sos_alert_fcm({"event_id": "evt-no-dev", "heart_rate": 72, "spo2": 98})
    assert result.success is False
    assert result.skipped_reason in ("no_devices", "disabled", "duplicate")


def test_deduplication_prevents_double_send():
    with patch.dict(os.environ, {"FCM_ENABLED": "true", "ANDROID_GATEWAY_ENABLED": "true"}):
        with patch.object(_svc(), "_is_duplicate_fcm_event", return_value=True):
            result = _svc().send_sos_alert_fcm({"event_id": "dedup-99", "heart_rate": 70, "spo2": 97})
    assert result.success is False
    assert result.skipped_reason == "duplicate"


def test_gateway_registration_valid_key():
    with patch.dict(os.environ, {"GATEWAY_API_KEY": "test-secret-key"}):
        result = _svc().register_gateway_device(token="a" * 20, device_name="Test Phone", api_key="test-secret-key")
    assert result["status"] == "registered"
    assert "doc_id" in result
    assert result["device_name"] == "Test Phone"


def test_gateway_registration_invalid_key():
    with patch.dict(os.environ, {"GATEWAY_API_KEY": "correct-key"}):
        raised = False
        try:
            _svc().register_gateway_device(token="a" * 20, device_name="Phone", api_key="wrong-key")
        except ValueError:
            raised = True
    assert raised


def test_gateway_registration_empty_token():
    with patch.dict(os.environ, {"GATEWAY_API_KEY": "correct-key"}):
        raised = False
        try:
            _svc().register_gateway_device(token="", device_name="Phone", api_key="correct-key")
        except ValueError:
            raised = True
    assert raised


def test_sms_status_update_valid_key():
    with patch.dict(os.environ, {"GATEWAY_API_KEY": "test-key"}):
        result = _svc().update_sms_delivery_status(
            event_id="evt1", status="SENT_TO_TELEPHONY",
            details={"recipient": "****1234"}, api_key="test-key",
        )
    assert result is False  # Firebase mocked as disabled — returns False, no exception


def test_sms_status_update_invalid_key():
    with patch.dict(os.environ, {"GATEWAY_API_KEY": "correct-key"}):
        raised = False
        try:
            _svc().update_sms_delivery_status("evt2", "FAILED", {}, api_key="bad-key")
        except ValueError:
            raised = True
    assert raised


def test_get_registered_gateways_no_firebase():
    assert _svc().get_registered_gateways() == []


def test_get_recent_alert_history_no_firebase():
    assert _svc().get_recent_alert_history(limit=5) == []


def test_get_recent_sms_statuses_no_firebase():
    assert _svc().get_recent_sms_statuses(limit=5) == []


def test_unregister_invalid_key():
    with patch.dict(os.environ, {"GATEWAY_API_KEY": "correct-key"}):
        raised = False
        try:
            _svc().unregister_gateway_device(token="a" * 20, api_key="wrong-key")
        except ValueError:
            raised = True
    assert raised


def test_fcm_result_dataclass_defaults():
    r = _svc().FCMResult(success=True, message="ok")
    assert r.failed_tokens == []
    assert r.message_id is None
    assert r.skipped_reason is None


def test_abnormal_alert_fcm_disabled():
    with patch.dict(os.environ, {"FCM_ENABLED": "false", "ANDROID_GATEWAY_ENABLED": "true"}):
        result = _svc().send_abnormal_alert_fcm(
            alert_condition="HIGH_HR", alert_reason="HR > 120",
            vitals={"heart_rate": 130, "spo2": 97, "timestamp": "2024-01-01T00:00:00Z"},
            event_id="abnormal_1",
        )
    assert result.success is False
    assert result.skipped_reason == "disabled"
