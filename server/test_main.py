"""
VEKSHA - Backend Unit Tests (v1.2.0)
Validates FastAPI endpoints, Pydantic validation, sustained anomaly triggers,
readings history, and SOS rate limiting.
"""

import sys
import pytest
from fastapi.testclient import TestClient
import main
from abnormal_detector import global_detector
from sms_service import reset_sms_cooldown


@pytest.fixture(autouse=True)
def reset_in_memory_state(monkeypatch):
    """Reset in-memory storage, detector, and SMS cooldown before each test."""
    monkeypatch.setenv("FIREBASE_ENABLED", "false")
    monkeypatch.setenv("SOS_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("SMS_ENABLED", "false")
    import firebase_service
    firebase_service.reset_firebase_state()
    main.firebase_service = firebase_service
    main.latest_reading = None
    main.recent_readings.clear()
    main.sos_events.clear()
    main._last_manual_sos_timestamp = 0.0
    global_detector.reset_state()
    reset_sms_cooldown()


@pytest.fixture
def client():
    """Return a TestClient bound to the active main app."""
    return TestClient(main.app)


def test_health_check(client):
    """Test health check endpoints."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "VEKSHA" in data["service"]
    assert "firebase" in data
    assert "sms" in data

    v1_response = client.get("/api/v1/health")
    assert v1_response.status_code == 200
    assert v1_response.json()["version"] == "1.2.0"


def test_submit_valid_reading(client):
    """Test POST /api/v1/readings with valid physiological data."""
    payload = {
        "heart_rate": 78.5,
        "spo2": 98.0,
        "sensor_status": "VALID_DATA",
        "device_id": "esp32-node-01",
    }
    response = client.post("/api/v1/readings", json=payload)
    assert response.status_code == 201
    res_data = response.json()
    assert res_data["status"] == "success"
    assert res_data["data"]["heart_rate"] == 78.5
    assert res_data["data"]["spo2"] == 98.0
    assert res_data["data"]["sensor_status"] == "VALID_DATA"


def test_submit_no_finger_reading(client):
    """Test POST /api/v1/readings when finger is removed."""
    payload = {
        "heart_rate": 0,
        "spo2": 0,
        "sensor_status": "NO_FINGER",
        "device_id": "esp32-node-01",
    }
    response = client.post("/api/v1/readings", json=payload)
    assert response.status_code == 201
    assert response.json()["data"]["sensor_status"] == "NO_FINGER"


def test_reject_valid_status_without_vitals(client):
    """Test that VALID_DATA requires non-null heart_rate and spo2."""
    payload = {
        "sensor_status": "VALID_DATA",
        "device_id": "esp32-node-01",
    }
    response = client.post("/api/v1/readings", json=payload)
    assert response.status_code == 422


def test_reject_out_of_bounds_vitals(client):
    """Test rejection of physiologically impossible vitals when VALID_DATA."""
    # HR too high
    r_high_hr = client.post("/api/v1/readings", json={
        "heart_rate": 250.0,
        "spo2": 98.0,
        "sensor_status": "VALID_DATA"
    })
    assert r_high_hr.status_code == 422

    # SpO2 too low
    r_low_o2 = client.post("/api/v1/readings", json={
        "heart_rate": 75.0,
        "spo2": 45.0,
        "sensor_status": "VALID_DATA"
    })
    assert r_low_o2.status_code == 422


def test_get_latest_reading(client):
    """Test GET /api/v1/readings/latest before and after submission."""
    res_empty = client.get("/api/v1/readings/latest")
    assert res_empty.status_code == 200
    assert res_empty.json()["status"] == "no_data"

    client.post("/api/v1/readings", json={
        "heart_rate": 72.0,
        "spo2": 99.0,
        "sensor_status": "VALID_DATA"
    })

    res_filled = client.get("/api/v1/readings/latest")
    assert res_filled.status_code == 200
    assert res_filled.json()["status"] == "success"
    assert res_filled.json()["data"]["heart_rate"] == 72.0


def test_get_readings_history(client):
    """Test GET /api/v1/readings/history returns chronologically ordered telemetry."""
    for hr in (70.0, 72.0, 74.0):
        client.post("/api/v1/readings", json={
            "heart_rate": hr,
            "spo2": 98.0,
            "sensor_status": "VALID_DATA"
        })

    res = client.get("/api/v1/readings/history?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["total"] == 3
    assert data["readings"][0]["heart_rate"] == 70.0
    assert data["readings"][-1]["heart_rate"] == 74.0


def test_config_thresholds_endpoint(client):
    """Test GET /api/v1/config/thresholds returns active alert settings."""
    res = client.get("/api/v1/config/thresholds")
    assert res.status_code == 200
    data = res.json()
    assert "thresholds" in data
    assert "hr_min" in data["thresholds"]
    assert "hr_max" in data["thresholds"]


# ---------------------------------------------------------------------------
# SOS Endpoint Tests
# ---------------------------------------------------------------------------

def test_trigger_sos_minimal(client):
    """Test POST /api/v1/sos with only required default fields."""
    response = client.post("/api/v1/sos", json={})
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "sos_triggered"
    event = data["data"]
    assert event["alert_status"] == "TRIGGERED"
    assert event["gps_location"] == "Not Available"
    assert event["device_id"] == "esp32-veksha"
    assert event["triggered_by"] == "manual_button"
    assert event["sms_sent"] is False
    assert "timestamp" in event
    assert event["event_id"] == 1


def test_trigger_sos_with_health_snapshot(client):
    """Test POST /api/v1/sos includes a health reading snapshot."""
    payload = {
        "device_id": "esp32-node-01",
        "triggered_by": "manual_button",
        "heart_rate": 88.5,
        "spo2": 96.2,
        "sensor_status": "VALID_DATA",
        "notes": "Chest pain",
    }
    response = client.post("/api/v1/sos", json=payload)
    assert response.status_code == 201
    event = response.json()["data"]
    assert event["heart_rate"] == 88.5
    assert event["spo2"] == 96.2
    assert event["sensor_status"] == "VALID_DATA"
    assert event["notes"] == "Chest pain"


def test_sos_cooldown_rate_limit(client, monkeypatch):
    """Test that rapid repeated SOS requests trigger HTTP 429."""
    monkeypatch.setenv("SOS_COOLDOWN_SECONDS", "15")

    # First SOS should succeed
    r1 = client.post("/api/v1/sos", json={})
    assert r1.status_code == 201

    # Second immediate SOS should be rejected with HTTP 429
    r2 = client.post("/api/v1/sos", json={})
    assert r2.status_code == 429
    assert "rate limit active" in r2.json()["detail"]


def test_sos_history_populated(client):
    """Test GET /api/v1/sos/history returns events in reverse-chronological order."""
    client.post("/api/v1/sos", json={"notes": "first"})
    client.post("/api/v1/sos", json={"notes": "second"})

    response = client.get("/api/v1/sos/history")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["events"][0]["notes"] == "second"
    assert data["events"][1]["notes"] == "first"


def test_sustained_abnormal_reading_triggers_auto_sos(client, monkeypatch):
    """Sustained abnormal vitals must auto-record an SOS alert event."""
    monkeypatch.setenv("ALERT_HR_MAX", "120.0")
    monkeypatch.setenv("ALERT_SUSTAINED_COUNT", "2")
    monkeypatch.setenv("ALERT_COOLDOWN_SECONDS", "300")

    high_hr_payload = {
        "heart_rate": 145.0,
        "spo2": 97.0,
        "sensor_status": "VALID_DATA",
        "device_id": "esp32-veksha",
    }

    # Frame 1: accumulating streak
    r1 = client.post("/api/v1/readings", json=high_hr_payload)
    assert r1.status_code == 201
    assert "auto_alert" not in r1.json()["data"]

    # Frame 2: sustained threshold reached -> auto-alert created!
    r2 = client.post("/api/v1/readings", json=high_hr_payload)
    assert r2.status_code == 201
    assert "auto_alert" in r2.json()["data"]
    assert r2.json()["data"]["auto_alert"]["condition"] == "TACHYCARDIA_HIGH_HR"

    # Verify auto SOS event was appended to history
    sos_res = client.get("/api/v1/sos/history")
    assert sos_res.status_code == 200
    assert sos_res.json()["total"] == 1
    assert sos_res.json()["events"][0]["triggered_by"] == "auto_sustained_abnormal"


# ---------------------------------------------------------------------------
# Gateway Endpoints Tests (FCM Android SMS Gateway)
# ---------------------------------------------------------------------------

def test_gateway_status_endpoint(client):
    """GET /api/v1/gateway/status returns gateway health and device counts."""
    res = client.get("/api/v1/gateway/status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "gateway_count" in data
    assert "gateways" in data
    assert "recent_sms_statuses" in data
    assert "recent_fcm_alerts" in data


def test_gateway_register_endpoint_success(client, monkeypatch):
    """POST /api/v1/gateway/register registers token with valid API key."""
    monkeypatch.setenv("GATEWAY_API_KEY", "test-valid-key")
    payload = {
        "token": "valid_fcm_token_1234567890",
        "device_name": "OnePlus Nord CE 2 Lite",
        "api_key": "test-valid-key",
    }
    res = client.post("/api/v1/gateway/register", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["status"] == "registered"
    assert data["data"]["device_name"] == "OnePlus Nord CE 2 Lite"


def test_gateway_register_endpoint_unauthorized(client, monkeypatch):
    """POST /api/v1/gateway/register rejects with 401 when API key is wrong."""
    monkeypatch.setenv("GATEWAY_API_KEY", "test-valid-key")
    payload = {
        "token": "valid_fcm_token_1234567890",
        "device_name": "OnePlus Nord CE 2 Lite",
        "api_key": "wrong-key",
    }
    res = client.post("/api/v1/gateway/register", json=payload)
    assert res.status_code == 401


def test_gateway_sms_status_endpoint_validation(client, monkeypatch):
    """POST /api/v1/gateway/sms-status validates status enum and updates delivery."""
    monkeypatch.setenv("GATEWAY_API_KEY", "test-valid-key")

    # Invalid status should return 422
    res_inv = client.post("/api/v1/gateway/sms-status", json={
        "event_id": "evt-123",
        "sms_status": "INVALID_STATUS",
        "api_key": "test-valid-key",
    })
    assert res_inv.status_code == 422

    # Valid status
    res_val = client.post("/api/v1/gateway/sms-status", json={
        "event_id": "evt-123",
        "sms_status": "SENT_TO_TELEPHONY",
        "recipient": "9876543210",
        "api_key": "test-valid-key",
    })
    assert res_val.status_code == 200
    assert res_val.json()["sms_status"] == "SENT_TO_TELEPHONY"


def test_gateway_alerts_endpoint(client):
    """GET /api/v1/gateway/alerts returns list of recent FCM alerts."""
    res = client.get("/api/v1/gateway/alerts?limit=5")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "alerts" in data
    assert "total" in data


def test_gateway_unregister_endpoint(client, monkeypatch):
    """DELETE /api/v1/gateway/unregister removes or deactivates gateway device."""
    monkeypatch.setenv("GATEWAY_API_KEY", "test-valid-key")
    res = client.delete("/api/v1/gateway/unregister?token=sample_token_123456789&api_key=test-valid-key")
    assert res.status_code == 200

