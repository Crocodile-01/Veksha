"""
Unit Tests for VEKSHA Abnormal Reading & Sustained Anomaly Detector
"""

import time
import pytest
from abnormal_detector import AbnormalReadingDetector, ThresholdConfig, AbnormalAlertEvent


@pytest.fixture
def detector(monkeypatch):
    """Provide a fresh detector with predictable thresholds."""
    monkeypatch.setenv("ALERT_HR_MIN", "50.0")
    monkeypatch.setenv("ALERT_HR_MAX", "120.0")
    monkeypatch.setenv("ALERT_SPO2_MIN", "90.0")
    monkeypatch.setenv("ALERT_SUSTAINED_COUNT", "3")
    monkeypatch.setenv("ALERT_COOLDOWN_SECONDS", "300")

    det = AbnormalReadingDetector()
    det.reset_state()
    return det


def test_normal_vitals_no_alert(detector):
    """Normal vitals should never trigger an alert."""
    reading = {
        "heart_rate": 75.0,
        "spo2": 98.0,
        "sensor_status": "VALID_DATA",
        "device_id": "esp32-test",
    }
    for _ in range(5):
        event = detector.evaluate_reading(reading)
        assert event is None


def test_single_noisy_spike_does_not_trigger_alert(detector):
    """A single high HR spike should NOT trigger an alert (false positive rejection)."""
    normal = {"heart_rate": 72.0, "spo2": 98.0, "sensor_status": "VALID_DATA"}
    spike = {"heart_rate": 150.0, "spo2": 98.0, "sensor_status": "VALID_DATA"}

    assert detector.evaluate_reading(normal) is None
    # 1st spike frame
    assert detector.evaluate_reading(spike) is None
    # Returns to normal -> resets counter
    assert detector.evaluate_reading(normal) is None
    # Another isolated spike -> count starts back at 1
    assert detector.evaluate_reading(spike) is None


def test_sustained_tachycardia_triggers_alert(detector):
    """3 consecutive high HR readings must confirm a sustained TACHYCARDIA alert."""
    high_hr = {"heart_rate": 135.0, "spo2": 97.0, "sensor_status": "VALID_DATA", "device_id": "esp32-node1"}

    # Frame 1: accumulating
    assert detector.evaluate_reading(high_hr) is None
    # Frame 2: accumulating
    assert detector.evaluate_reading(high_hr) is None
    # Frame 3: confirmed sustained!
    alert = detector.evaluate_reading(high_hr)

    assert alert is not None
    assert alert.condition == "TACHYCARDIA_HIGH_HR"
    assert "Heart rate high" in alert.reason
    assert alert.consecutive_frames == 3
    assert alert.device_id == "esp32-node1"


def test_sustained_hypoxia_triggers_alert(detector):
    """3 consecutive low SpO2 readings must confirm a sustained HYPOXIA alert."""
    low_o2 = {"heart_rate": 80.0, "spo2": 88.0, "sensor_status": "VALID_DATA", "device_id": "esp32-node1"}

    assert detector.evaluate_reading(low_o2) is None
    assert detector.evaluate_reading(low_o2) is None
    alert = detector.evaluate_reading(low_o2)

    assert alert is not None
    assert alert.condition == "HYPOXIA_LOW_SPO2"
    assert "Blood oxygen low" in alert.reason


def test_sensor_error_resets_streak(detector):
    """Sensor operational errors (NO_FINGER, INVALID_DATA) must reset any abnormal count."""
    high_hr = {"heart_rate": 140.0, "spo2": 97.0, "sensor_status": "VALID_DATA"}
    no_finger = {"heart_rate": 0, "spo2": 0, "sensor_status": "NO_FINGER"}

    # 2 high readings accumulated
    assert detector.evaluate_reading(high_hr) is None
    assert detector.evaluate_reading(high_hr) is None

    # Finger removed -> streak must be reset
    assert detector.evaluate_reading(no_finger) is None

    # Re-placed finger on next frame: must start from 1 again, not trigger on frame 1
    assert detector.evaluate_reading(high_hr) is None


def test_cooldown_prevents_immediate_duplicate_alerts(detector):
    """After an alert fires, cooldown must suppress subsequent alerts within the window."""
    high_hr = {"heart_rate": 140.0, "spo2": 97.0, "sensor_status": "VALID_DATA"}

    # Trigger initial alert (frames 1, 2, 3)
    detector.evaluate_reading(high_hr)
    detector.evaluate_reading(high_hr)
    initial_alert = detector.evaluate_reading(high_hr)
    assert initial_alert is not None

    # Continue high readings: cooldown active, so should return None
    for _ in range(5):
        assert detector.evaluate_reading(high_hr) is None

