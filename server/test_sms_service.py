"""
Unit Tests for VEKSHA SMS Service & Indian Gateway Abstraction
"""

import os
from unittest.mock import MagicMock, patch
import pytest

import sms_service
from sms_service import (
    BaseSmsProvider,
    MockSmsProvider,
    MSG91SmsProvider,
    SmsResult,
    dispatch_emergency_sms,
    get_emergency_contacts,
    is_sms_enabled,
    reset_sms_cooldown,
    set_sms_provider,
)


@pytest.fixture(autouse=True)
def clean_sms_state(monkeypatch):
    """Reset cooldowns and provider before each test."""
    reset_sms_cooldown()
    sms_service._provider_instance = None
    monkeypatch.setenv("SMS_ENABLED", "true")
    monkeypatch.setenv("EMERGENCY_CONTACTS", "919876543210, 919876543211")
    monkeypatch.setenv("SMS_COOLDOWN_SECONDS", "300")


def test_mock_provider_dispatch():
    """Mock provider must record dispatches and return success."""
    provider = MockSmsProvider()
    set_sms_provider(provider)

    vitals = {"heart_rate": 88.0, "spo2": 96.5, "device_id": "esp32-node1"}
    res = dispatch_emergency_sms(
        event_type="SOS_MANUAL",
        vitals=vitals,
        reason="Manual button pressed",
    )

    assert res.success is True
    assert res.provider == "mock"
    assert len(provider.sent_log) == 1
    assert provider.sent_log[0]["event_type"] == "SOS_MANUAL"
    assert "919876543210" in provider.sent_log[0]["recipients"]


def test_sms_disabled_returns_clean_result(monkeypatch):
    """When SMS_ENABLED=false, dispatch should return cleanly without sending."""
    monkeypatch.setenv("SMS_ENABLED", "false")
    provider = MockSmsProvider()
    set_sms_provider(provider)

    res = dispatch_emergency_sms(
        event_type="SOS_MANUAL",
        vitals={"heart_rate": 70.0},
    )

    assert res.success is False
    assert res.provider == "disabled"
    assert len(provider.sent_log) == 0


def test_missing_contacts_returns_error(monkeypatch):
    """When no emergency contacts are configured, return clear error."""
    monkeypatch.setenv("EMERGENCY_CONTACTS", "")
    provider = MockSmsProvider()
    set_sms_provider(provider)

    res = dispatch_emergency_sms(
        event_type="SOS_MANUAL",
        vitals={"heart_rate": 70.0},
    )

    assert res.success is False
    assert "No emergency contacts" in res.message


def test_cooldown_suppresses_repeated_dispatch():
    """Consecutive dispatches within cooldown window must be suppressed."""
    provider = MockSmsProvider()
    set_sms_provider(provider)

    vitals = {"heart_rate": 90.0, "spo2": 97.0}

    # First dispatch succeeds
    res1 = dispatch_emergency_sms(event_type="SOS_MANUAL", vitals=vitals)
    assert res1.success is True

    # Immediate second dispatch is suppressed by cooldown
    res2 = dispatch_emergency_sms(event_type="SOS_MANUAL", vitals=vitals)
    assert res2.success is False
    assert res2.cooldown_active is True
    assert res2.cooldown_remaining_seconds > 0
    assert len(provider.sent_log) == 1


def test_msg91_sanitization():
    """MSG91 provider must sanitize Indian phone numbers."""
    provider = MSG91SmsProvider(auth_key="test_key", template_id="test_tmpl")
    # 10-digit number should have 91 prepended
    assert provider._sanitize_phone("9876543210") == "919876543210"
    # Number with + and spaces
    assert provider._sanitize_phone("+91 98765 43210") == "919876543210"


def test_msg91_missing_credentials():
    """MSG91 provider must fail safely when credentials are missing."""
    provider = MSG91SmsProvider(auth_key="", template_id="")
    res = provider.send_alert(
        recipients=["919876543210"],
        event_type="SOS",
        vitals={},
    )
    assert res.success is False
    assert "credentials missing" in res.message

