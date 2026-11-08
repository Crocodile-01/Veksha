"""
VEKSHA - Abnormal Reading & Sustained Anomaly Detector
======================================================
Monitors physiological telemetry streams to detect sustained vitals abnormalities:
  - Tachycardia (Heart Rate > ALERT_HR_MAX)
  - Bradycardia (Heart Rate < ALERT_HR_MIN)
  - Hypoxia (SpO2 < ALERT_SPO2_MIN)

Design Principles:
  1. False-Positive Immunity:
     Single noisy spikes never trigger alerts. Requires N consecutive valid frames
     (default: 3) or sustained abnormal duration before confirming an alert.
  2. Clear Error Distinction:
     Sensor operational errors (NO_FINGER, INVALID_DATA, CALIBRATING) are strictly
     segregated and reset the abnormal counter immediately.
  3. Cooldown & Anti-Spam:
     Once an alert fires for an abnormality, a cooldown period (default: 300s)
     prevents duplicate alert floods to family members and cloud databases.
  4. Clinician-Configurable:
     All thresholds are loaded from environment variables (.env) with safe
     conservative baseline defaults. No hard-coded assumptions.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class AbnormalAlertEvent:
    """Represents a confirmed, sustained physiological abnormality."""
    condition: str             # e.g., "TACHYCARDIA_HIGH_HR", "HYPOXIA_LOW_SPO2"
    reason: str                # Human-readable explanation
    heart_rate: Optional[float]
    spo2: Optional[float]
    consecutive_frames: int
    device_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "condition": self.condition,
            "reason": self.reason,
            "heart_rate": self.heart_rate,
            "spo2": self.spo2,
            "consecutive_frames": self.consecutive_frames,
            "device_id": self.device_id,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Configuration Helper
# ---------------------------------------------------------------------------

class ThresholdConfig:
    """Loads and encapsulates configurable alert thresholds."""

    @classmethod
    def get_hr_min(cls) -> float:
        try:
            return float(os.getenv("ALERT_HR_MIN", "50.0"))
        except ValueError:
            return 50.0

    @classmethod
    def get_hr_max(cls) -> float:
        try:
            return float(os.getenv("ALERT_HR_MAX", "120.0"))
        except ValueError:
            return 120.0

    @classmethod
    def get_spo2_min(cls) -> float:
        try:
            return float(os.getenv("ALERT_SPO2_MIN", "90.0"))
        except ValueError:
            return 90.0

    @classmethod
    def get_sustained_count(cls) -> int:
        try:
            return max(2, int(os.getenv("ALERT_SUSTAINED_COUNT", "3")))
        except ValueError:
            return 3

    @classmethod
    def get_cooldown_seconds(cls) -> int:
        try:
            return int(os.getenv("ALERT_COOLDOWN_SECONDS", "300"))
        except ValueError:
            return 300


# ---------------------------------------------------------------------------
# Detector Engine
# ---------------------------------------------------------------------------

class AbnormalReadingDetector:
    """
    Stateful detector analyzing sequential readings for sustained abnormalities.
    """

    def __init__(self):
        self._consecutive_counts: Dict[str, int] = {}
        self._last_alert_timestamps: Dict[str, float] = {}

    def reset_state(self) -> None:
        """Clear all active counters and cooldown timers (used in tests or on device reset)."""
        self._consecutive_counts.clear()
        self._last_alert_timestamps.clear()

    def evaluate_reading(self, reading: Dict[str, Any]) -> Optional[AbnormalAlertEvent]:
        """
        Process a single incoming telemetry frame.

        Parameters
        ----------
        reading:
            Dictionary matching HealthReadingInput schema:
            {"heart_rate": float, "spo2": float, "sensor_status": str, "device_id": str}

        Returns
        -------
        AbnormalAlertEvent if a sustained abnormal condition is confirmed and cooldown has elapsed.
        None if vitals are normal, during buffer accumulation, or if status is not VALID_DATA.
        """
        status = reading.get("sensor_status")

        # 1. Error / Non-valid data handling:
        # Non-valid data (e.g. finger removed or calibrating) immediately resets abnormal streaks
        if status != "VALID_DATA":
            if self._consecutive_counts:
                logger.debug(
                    "[AbnormalDetector] Status is '%s'. Resetting active abnormal streaks.", status
                )
                self._consecutive_counts.clear()
            return None

        hr = reading.get("heart_rate")
        spo2 = reading.get("spo2")
        device_id = reading.get("device_id") or "esp32-veksha"

        if hr is None or spo2 is None:
            return None

        hr_min = ThresholdConfig.get_hr_min()
        hr_max = ThresholdConfig.get_hr_max()
        spo2_min = ThresholdConfig.get_spo2_min()
        sustained_required = ThresholdConfig.get_sustained_count()
        cooldown_window = ThresholdConfig.get_cooldown_seconds()

        # Identify current instantaneous abnormal conditions
        detected_conditions: List[tuple[str, str]] = []

        if hr > hr_max:
            detected_conditions.append(
                ("TACHYCARDIA_HIGH_HR", f"Heart rate high ({hr:.1f} BPM > threshold {hr_max:.1f} BPM)")
            )
        elif hr < hr_min:
            detected_conditions.append(
                ("BRADYCARDIA_LOW_HR", f"Heart rate low ({hr:.1f} BPM < threshold {hr_min:.1f} BPM)")
            )

        if spo2 < spo2_min:
            detected_conditions.append(
                ("HYPOXIA_LOW_SPO2", f"Blood oxygen low ({spo2:.1f}% < threshold {spo2_min:.1f}%)")
            )

        # If vitals are completely normal, clear streaks
        if not detected_conditions:
            if self._consecutive_counts:
                self._consecutive_counts.clear()
            return None

        now = time.time()
        confirmed_event: Optional[AbnormalAlertEvent] = None

        # Track consecutive counts for each detected abnormal condition
        for cond_key, reason_text in detected_conditions:
            count = self._consecutive_counts.get(cond_key, 0) + 1
            self._consecutive_counts[cond_key] = count

            logger.info(
                "[AbnormalDetector] Condition '%s' streak: %d/%d frames",
                cond_key, count, sustained_required
            )

            # Check if sustained count has been satisfied
            if count >= sustained_required:
                last_alert_time = self._last_alert_timestamps.get(cond_key, 0.0)
                elapsed = now - last_alert_time

                if elapsed >= cooldown_window:
                    # Fire alert!
                    self._last_alert_timestamps[cond_key] = now
                    # Reset counter after triggering so next alert requires full streak again
                    self._consecutive_counts[cond_key] = 0

                    confirmed_event = AbnormalAlertEvent(
                        condition=cond_key,
                        reason=f"Sustained {reason_text} for {count} consecutive frames.",
                        heart_rate=hr,
                        spo2=spo2,
                        consecutive_frames=count,
                        device_id=device_id,
                    )
                    logger.warning("[AbnormalDetector] CONFIRMED ALERT: %s", confirmed_event.reason)
                    # Trigger the highest severity condition
                    break
                else:
                    logger.debug(
                        "[AbnormalDetector] Alert for '%s' suppressed by cooldown (elapsed %.1fs < %ds)",
                        cond_key, elapsed, cooldown_window
                    )

        # Clean up conditions that are no longer present
        current_condition_keys = {c[0] for c in detected_conditions}
        for existing_key in list(self._consecutive_counts.keys()):
            if existing_key not in current_condition_keys:
                del self._consecutive_counts[existing_key]

        return confirmed_event


# Singleton detector instance for backend
global_detector = AbnormalReadingDetector()

