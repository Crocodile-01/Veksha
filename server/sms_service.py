"""
VEKSHA - Emergency SMS Notification Service
===========================================
Modular SMS notification layer supporting:
  - Clean provider abstraction (BaseSmsProvider)
  - MSG91 Indian SMS Gateway (DLT compliant via Flow API)
  - MockSmsProvider for safe offline local testing & automated pytest runs
  - Multiple emergency contact dispatch
  - Anti-spam cooldown timer to prevent SMS loops and wallet depletion
  - Delivery response tracking and structured error handling
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error
import json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class SmsResult:
    """Structured response returned by any SMS provider."""
    success: bool
    provider: str
    message: str
    recipients_sent: List[str] = field(default_factory=list)
    recipients_failed: List[str] = field(default_factory=list)
    cooldown_active: bool = False
    cooldown_remaining_seconds: int = 0
    status_code: Optional[int] = None
    raw_response: Optional[Dict[str, Any]] = None
    dispatched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "provider": self.provider,
            "message": self.message,
            "recipients_sent": self.recipients_sent,
            "recipients_failed": self.recipients_failed,
            "cooldown_active": self.cooldown_active,
            "cooldown_remaining_seconds": self.cooldown_remaining_seconds,
            "status_code": self.status_code,
            "dispatched_at": self.dispatched_at,
        }


# ---------------------------------------------------------------------------
# Provider Interface
# ---------------------------------------------------------------------------

class BaseSmsProvider(ABC):
    """Abstract SMS Gateway Interface."""

    @abstractmethod
    def send_alert(
        self,
        recipients: List[str],
        event_type: str,
        vitals: Dict[str, Any],
        reason: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> SmsResult:
        """Dispatch emergency alert SMS to the list of recipient numbers."""
        pass


# ---------------------------------------------------------------------------
# Mock Provider (Safe for Dev, Offline & Pytest)
# ---------------------------------------------------------------------------

class MockSmsProvider(BaseSmsProvider):
    """Simulated provider that logs SMS dispatches without external API calls."""

    def __init__(self):
        self.sent_log: List[Dict[str, Any]] = []

    def send_alert(
        self,
        recipients: List[str],
        event_type: str,
        vitals: Dict[str, Any],
        reason: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> SmsResult:
        payload = {
            "recipients": recipients,
            "event_type": event_type,
            "vitals": vitals,
            "reason": reason,
            "notes": notes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.sent_log.append(payload)
        logger.info(
            "[SMS Mock] Simulated SMS dispatched to %s | Type: %s | Reason: %s",
            recipients, event_type, reason
        )
        return SmsResult(
            success=True,
            provider="mock",
            message=f"Mock SMS sent successfully to {len(recipients)} contacts.",
            recipients_sent=recipients,
            status_code=200,
            raw_response={"status": "mock_success", "payload": payload},
        )


# ---------------------------------------------------------------------------
# MSG91 Provider (India DLT Flow API)
# ---------------------------------------------------------------------------

class MSG91SmsProvider(BaseSmsProvider):
    """
    MSG91 Flow API Provider for India.
    Conforms to TRAI DLT regulations by using registered Template ID (Flow ID).
    """

    def __init__(
        self,
        auth_key: Optional[str] = None,
        template_id: Optional[str] = None,
        sender_id: Optional[str] = None,
    ):
        self.auth_key = auth_key or os.getenv("MSG91_AUTH_KEY", "").strip()
        self.template_id = template_id or os.getenv("MSG91_TEMPLATE_ID", "").strip()
        self.sender_id = sender_id or os.getenv("MSG91_SENDER_ID", "VEKSHA").strip()
        self.api_url = os.getenv("MSG91_API_URL", "https://control.msg91.com/api/v5/flow/").strip()

    def _sanitize_phone(self, phone: str) -> str:
        """Strip spaces, hyphens, and leading + for standard international digits."""
        cleaned = "".join(ch for ch in phone if ch.isdigit())
        # Default 10-digit Indian numbers to prepend country code 91
        if len(cleaned) == 10:
            return f"91{cleaned}"
        return cleaned

    def send_alert(
        self,
        recipients: List[str],
        event_type: str,
        vitals: Dict[str, Any],
        reason: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> SmsResult:
        if not self.auth_key or not self.template_id:
            msg = "MSG91 credentials missing (MSG91_AUTH_KEY or MSG91_TEMPLATE_ID not set)."
            logger.error("[SMS MSG91] %s", msg)
            return SmsResult(
                success=False,
                provider="msg91",
                message=msg,
                recipients_failed=recipients,
                status_code=400,
            )

        sanitized_recipients = [self._sanitize_phone(r) for r in recipients if r.strip()]
        if not sanitized_recipients:
            return SmsResult(
                success=False,
                provider="msg91",
                message="No valid emergency contact phone numbers provided.",
                recipients_failed=recipients,
                status_code=400,
            )

        hr_str = str(round(vitals.get("heart_rate") or 0)) if vitals.get("heart_rate") is not None else "N/A"
        spo2_str = f"{vitals.get('spo2'):.1f}" if vitals.get("spo2") is not None else "N/A"
        device_id = vitals.get("device_id") or "esp32-veksha"
        alert_reason = reason or event_type

        # Construct MSG91 flow recipients array
        # Standard variables mapped to DLT template: var1=type, var2=device, var3=hr, var4=spo2, var5=reason
        flow_recipients = []
        for phone in sanitized_recipients:
            flow_recipients.append({
                "mobiles": phone,
                "var1": event_type,
                "var2": device_id,
                "var3": hr_str,
                "var4": spo2_str,
                "var5": alert_reason,
            })

        payload = {
            "template_id": self.template_id,
            "short_url": "0",
            "recipients": flow_recipients,
        }

        if self.sender_id:
            payload["sender"] = self.sender_id

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            data=data_bytes,
            headers={
                "authkey": self.auth_key,
                "content-type": "application/json",
                "accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                resp_status = resp.status
                resp_body = resp.read().decode("utf-8")
                try:
                    parsed_body = json.loads(resp_body)
                except Exception:
                    parsed_body = {"raw": resp_body}

                if 200 <= resp_status < 300:
                    logger.info("[SMS MSG91] Dispatched successfully to %s: %s", sanitized_recipients, parsed_body)
                    return SmsResult(
                        success=True,
                        provider="msg91",
                        message=f"MSG91 SMS alert dispatched to {len(sanitized_recipients)} contact(s).",
                        recipients_sent=sanitized_recipients,
                        status_code=resp_status,
                        raw_response=parsed_body,
                    )
                else:
                    logger.warning("[SMS MSG91] Non-200 response: %s (status=%d)", resp_body, resp_status)
                    return SmsResult(
                        success=False,
                        provider="msg91",
                        message=f"MSG91 returned status {resp_status}: {resp_body}",
                        recipients_failed=sanitized_recipients,
                        status_code=resp_status,
                        raw_response=parsed_body,
                    )
        except urllib.error.HTTPError as http_err:
            error_body = http_err.read().decode("utf-8", errors="replace")
            logger.error("[SMS MSG91] HTTP Error %d: %s", http_err.code, error_body)
            return SmsResult(
                success=False,
                provider="msg91",
                message=f"MSG91 API error ({http_err.code}): {error_body}",
                recipients_failed=sanitized_recipients,
                status_code=http_err.code,
                raw_response={"error": error_body},
            )
        except Exception as exc:
            logger.error("[SMS MSG91] Network exception: %s", exc)
            return SmsResult(
                success=False,
                provider="msg91",
                message=f"Failed to connect to MSG91: {exc}",
                recipients_failed=sanitized_recipients,
                status_code=500,
            )


# ---------------------------------------------------------------------------
# Global Service Manager & Anti-Spam Cooldown Tracker
# ---------------------------------------------------------------------------

_last_sms_dispatch_time: float = 0.0
_provider_instance: Optional[BaseSmsProvider] = None


def get_emergency_contacts() -> List[str]:
    """Parse comma-separated emergency contacts from environment."""
    raw_contacts = os.getenv("EMERGENCY_CONTACTS", "").strip()
    if not raw_contacts:
        return []
    return [c.strip() for c in raw_contacts.split(",") if c.strip()]


def get_sms_provider() -> BaseSmsProvider:
    """Factory to retrieve or instantiate configured SMS provider."""
    global _provider_instance
    provider_name = os.getenv("SMS_PROVIDER", "mock").strip().lower()

    if _provider_instance is not None:
        return _provider_instance

    if provider_name == "msg91":
        _provider_instance = MSG91SmsProvider()
    else:
        _provider_instance = MockSmsProvider()

    return _provider_instance


def set_sms_provider(provider: BaseSmsProvider) -> None:
    """Override provider (useful for unit testing)."""
    global _provider_instance
    _provider_instance = provider


def reset_sms_cooldown() -> None:
    """Reset cooldown tracker (useful for testing)."""
    global _last_sms_dispatch_time
    _last_sms_dispatch_time = 0.0


def is_sms_enabled() -> bool:
    """Check if SMS notifications are enabled via environment."""
    return os.getenv("SMS_ENABLED", "false").strip().lower() in ("true", "1", "yes")


def get_cooldown_seconds() -> int:
    """Get cooldown window in seconds (default: 300s = 5 mins)."""
    try:
        return int(os.getenv("SMS_COOLDOWN_SECONDS", "300"))
    except ValueError:
        return 300


def dispatch_emergency_sms(
    event_type: str,
    vitals: Dict[str, Any],
    reason: Optional[str] = None,
    notes: Optional[str] = None,
    force: bool = False,
) -> SmsResult:
    """
    High-level entry point to send an emergency SMS alert with anti-spam cooldown.

    Parameters
    ----------
    event_type:
        e.g. "SOS_MANUAL_ALERT" or "SUSTAINED_ABNORMAL_READING"
    vitals:
        Dictionary containing heart_rate, spo2, device_id, etc.
    reason:
        Human-readable reason for the alert.
    notes:
        Optional patient note or location detail.
    force:
        If True, ignores cooldown (used for manual critical overrides).
    """
    global _last_sms_dispatch_time

    if not is_sms_enabled():
        return SmsResult(
            success=False,
            provider="disabled",
            message="SMS alerts are disabled in server/.env (SMS_ENABLED=false).",
        )

    contacts = get_emergency_contacts()
    if not contacts:
        return SmsResult(
            success=False,
            provider="none",
            message="No emergency contacts configured in server/.env (EMERGENCY_CONTACTS).",
        )

    cooldown_window = get_cooldown_seconds()
    now = time.time()
    elapsed = now - _last_sms_dispatch_time

    if not force and elapsed < cooldown_window:
        remaining = int(cooldown_window - elapsed)
        logger.warning(
            "[SMS] Dispatch suppressed by cooldown window. Remaining: %d seconds.", remaining
        )
        return SmsResult(
            success=False,
            provider="cooldown_guard",
            message=f"SMS suppressed by cooldown. Try again in {remaining} seconds.",
            cooldown_active=True,
            cooldown_remaining_seconds=remaining,
        )

    provider = get_sms_provider()
    result = provider.send_alert(
        recipients=contacts,
        event_type=event_type,
        vitals=vitals,
        reason=reason,
        notes=notes,
    )

    if result.success:
        _last_sms_dispatch_time = now

    return result

