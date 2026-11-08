"""
VEKSHA - Firebase Service Unit Tests
=====================================
Tests for firebase_service.py covering:
  - Initialisation with valid / invalid config
  - Graceful degradation when firebase-admin is not installed
  - store_reading / store_sos_event no-op behaviour when disabled
  - firebase_status() output shape in all states
  - Integration guard: main.py endpoints work correctly when Firebase is offline

All tests use unittest.mock to avoid any real network calls.
No actual Firebase credentials or project IDs are required to run these tests.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to reset firebase_service module state between tests
# ---------------------------------------------------------------------------

def _reload_firebase_service():
    """Reset firebase_service module-level globals in place."""
    import firebase_service
    firebase_service.reset_firebase_state()
    try:
        import main
        main.firebase_service = firebase_service
    except ImportError:
        pass
    return firebase_service


def _reload_main():
    """Reset main in-memory state."""
    import main
    import firebase_service
    firebase_service.reset_firebase_state()
    main.firebase_service = firebase_service
    main.latest_reading = None
    main.recent_readings.clear()
    main.sos_events.clear()
    return main


# ---------------------------------------------------------------------------
# 1. Initialisation tests
# ---------------------------------------------------------------------------

class TestFirebaseInitialisation:

    def test_disabled_via_env_var(self, monkeypatch):
        """FIREBASE_ENABLED=false must disable Firebase without errors."""
        monkeypatch.setenv("FIREBASE_ENABLED", "false")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", "")
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
        fs = _reload_firebase_service()
        fs.initialise_firebase()
        status = fs.firebase_status()
        assert status["firebase_enabled"] is False
        assert status["firebase_connected"] is False

    def test_missing_service_account_path(self, monkeypatch):
        """Missing path env var must disable Firebase gracefully."""
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", "")
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
        fs = _reload_firebase_service()
        fs.initialise_firebase()
        status = fs.firebase_status()
        assert status["firebase_connected"] is False
        assert "FIREBASE_SERVICE_ACCOUNT_PATH" in status["firebase_error"]

    def test_service_account_file_not_found(self, monkeypatch, tmp_path):
        """Non-existent credentials file must disable Firebase gracefully."""
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(tmp_path / "nonexistent.json"))
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project")
        fs = _reload_firebase_service()
        fs.initialise_firebase()
        status = fs.firebase_status()
        assert status["firebase_connected"] is False
        assert "not found" in status["firebase_error"].lower()

    def test_missing_project_id(self, monkeypatch, tmp_path):
        """Missing project ID must disable Firebase gracefully."""
        # Create a real (dummy) file so the path check passes
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text("{}")
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(fake_sa))
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "")
        fs = _reload_firebase_service()
        fs.initialise_firebase()
        status = fs.firebase_status()
        assert status["firebase_connected"] is False
        assert "FIREBASE_PROJECT_ID" in status["firebase_error"]

    def test_placeholder_project_id_rejected(self, monkeypatch, tmp_path):
        """Placeholder project ID value must be rejected."""
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text("{}")
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(fake_sa))
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "your-firebase-project-id-here")
        fs = _reload_firebase_service()
        fs.initialise_firebase()
        status = fs.firebase_status()
        assert status["firebase_connected"] is False

    def test_firebase_admin_not_installed(self, monkeypatch, tmp_path):
        """ImportError for firebase-admin must disable Firebase gracefully."""
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text("{}")
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(fake_sa))
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test-project-id")

        fs = _reload_firebase_service()

        # Simulate firebase_admin not being importable
        with patch.dict(sys.modules, {"firebase_admin": None, "firebase_admin.credentials": None, "firebase_admin.firestore": None}):
            fs.initialise_firebase()

        status = fs.firebase_status()
        assert status["firebase_connected"] is False
        assert "firebase-admin" in status["firebase_error"].lower()

    def test_successful_init_with_mocked_sdk(self, monkeypatch, tmp_path):
        """Successful initialisation with fully mocked firebase-admin SDK."""
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text("{}")
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(fake_sa))
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "veksha-test-project")

        fs = _reload_firebase_service()

        mock_db = MagicMock()
        mock_cred = MagicMock()
        mock_firebase_admin = MagicMock()
        mock_firebase_admin._apps = {}  # empty → triggers initialize_app
        mock_credentials_module = MagicMock()
        mock_credentials_module.Certificate.return_value = mock_cred
        mock_firestore_module = MagicMock()
        mock_firestore_module.client.return_value = mock_db

        with patch.dict(sys.modules, {
            "firebase_admin": mock_firebase_admin,
            "firebase_admin.credentials": mock_credentials_module,
            "firebase_admin.firestore": mock_firestore_module,
        }):
            fs.initialise_firebase()

        status = fs.firebase_status()
        assert status["firebase_enabled"] is True
        assert status["firebase_connected"] is True
        assert status["firebase_project"] == "veksha-test-project"


# ---------------------------------------------------------------------------
# 2. store_reading / store_sos_event tests
# ---------------------------------------------------------------------------

class TestStoreReading:

    def _get_disabled_fs(self, monkeypatch):
        monkeypatch.setenv("FIREBASE_ENABLED", "false")
        fs = _reload_firebase_service()
        fs.initialise_firebase()
        return fs

    def _get_enabled_fs(self, monkeypatch, tmp_path, mock_db):
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text("{}")
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(fake_sa))
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "veksha-test")
        fs = _reload_firebase_service()

        mock_firebase_admin = MagicMock()
        mock_firebase_admin._apps = {}
        mock_credentials_module = MagicMock()
        mock_firestore_module = MagicMock()
        mock_firestore_module.client.return_value = mock_db
        mock_firestore_module.SERVER_TIMESTAMP = "SERVER_TS"

        with patch.dict(sys.modules, {
            "firebase_admin": mock_firebase_admin,
            "firebase_admin.credentials": mock_credentials_module,
            "firebase_admin.firestore": mock_firestore_module,
        }):
            fs.initialise_firebase()
            fs._db = mock_db
            fs._firebase_enabled = True
        return fs, mock_firebase_admin, mock_credentials_module, mock_firestore_module

    def test_store_reading_noop_when_disabled(self, monkeypatch):
        """store_reading must return None and not raise when Firebase is disabled."""
        fs = self._get_disabled_fs(monkeypatch)
        result = fs.store_reading({"heart_rate": 72.0, "spo2": 98.0, "sensor_status": "VALID_DATA"})
        assert result is None

    def test_store_sos_noop_when_disabled(self, monkeypatch):
        """store_sos_event must return None and not raise when Firebase is disabled."""
        fs = self._get_disabled_fs(monkeypatch)
        result = fs.store_sos_event({"event_id": 1, "alert_status": "TRIGGERED"})
        assert result is None

    def test_store_reading_returns_doc_id_on_success(self, monkeypatch, tmp_path):
        """store_reading must return Firestore document ID on success."""
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc_ref.id = "abc123"
        mock_db.collection.return_value.add.return_value = (MagicMock(), mock_doc_ref)

        fs, *_ = self._get_enabled_fs(monkeypatch, tmp_path, mock_db)

        # Inject mock db and enabled flag directly — firebase_admin is not installed
        fs._db = mock_db
        fs._firebase_enabled = True

        mock_fs_module = MagicMock()
        mock_fs_module.SERVER_TIMESTAMP = "SERVER_TS"
        with patch.dict(sys.modules, {
            "firebase_admin": MagicMock(),
            "firebase_admin.firestore": mock_fs_module,
        }):
            result = fs.store_reading({"heart_rate": 72.0, "spo2": 98.0})

        assert result == "abc123"

    def test_store_sos_returns_doc_id_on_success(self, monkeypatch, tmp_path):
        """store_sos_event must return Firestore document ID on success."""
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc_ref.id = "sos999"
        mock_db.collection.return_value.add.return_value = (MagicMock(), mock_doc_ref)

        fs, *_ = self._get_enabled_fs(monkeypatch, tmp_path, mock_db)

        # Inject mock db and enabled flag directly — firebase_admin is not installed
        fs._db = mock_db
        fs._firebase_enabled = True

        mock_fs_module = MagicMock()
        mock_fs_module.SERVER_TIMESTAMP = "SERVER_TS"
        with patch.dict(sys.modules, {
            "firebase_admin": MagicMock(),
            "firebase_admin.firestore": mock_fs_module,
        }):
            result = fs.store_sos_event({"event_id": 1, "alert_status": "TRIGGERED"})

        assert result == "sos999"

    def test_store_reading_returns_none_on_firestore_exception(self, monkeypatch, tmp_path):
        """store_reading must return None (not raise) when Firestore throws."""
        mock_db = MagicMock()
        mock_db.collection.return_value.add.side_effect = Exception("Firestore timeout")

        fs, *_ = self._get_enabled_fs(monkeypatch, tmp_path, mock_db)

        mock_fs_module = MagicMock()
        mock_fs_module.SERVER_TIMESTAMP = "SERVER_TS"
        with patch.dict(sys.modules, {"firebase_admin.firestore": mock_fs_module}):
            result = fs.store_reading({"heart_rate": 72.0})

        assert result is None  # Must swallow the exception

    def test_store_sos_returns_none_on_firestore_exception(self, monkeypatch, tmp_path):
        """store_sos_event must return None (not raise) when Firestore throws."""
        mock_db = MagicMock()
        mock_db.collection.return_value.add.side_effect = ConnectionError("Network error")

        fs, *_ = self._get_enabled_fs(monkeypatch, tmp_path, mock_db)

        mock_fs_module = MagicMock()
        mock_fs_module.SERVER_TIMESTAMP = "SERVER_TS"
        with patch.dict(sys.modules, {"firebase_admin.firestore": mock_fs_module}):
            result = fs.store_sos_event({"event_id": 1})

        assert result is None


# ---------------------------------------------------------------------------
# 3. firebase_status tests
# ---------------------------------------------------------------------------

class TestFirebaseStatus:

    def test_status_when_disabled(self, monkeypatch):
        monkeypatch.setenv("FIREBASE_ENABLED", "false")
        fs = _reload_firebase_service()
        fs.initialise_firebase()
        s = fs.firebase_status()
        assert "firebase_enabled" in s
        assert "firebase_connected" in s
        assert s["firebase_connected"] is False

    def test_status_contains_error_when_credentials_missing(self, monkeypatch):
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", "")
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "test")
        fs = _reload_firebase_service()
        fs.initialise_firebase()
        s = fs.firebase_status()
        assert "firebase_error" in s
        assert len(s["firebase_error"]) > 0

    def test_status_fields_when_connected(self, monkeypatch, tmp_path):
        fake_sa = tmp_path / "sa.json"
        fake_sa.write_text("{}")
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_PATH", str(fake_sa))
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "veksha-test-project")
        monkeypatch.setenv("FIREBASE_READINGS_COLLECTION", "health_readings")
        monkeypatch.setenv("FIREBASE_SOS_COLLECTION", "sos_events")

        fs = _reload_firebase_service()

        mock_firebase_admin = MagicMock()
        mock_firebase_admin._apps = {}
        mock_firestore_module = MagicMock()
        mock_firestore_module.client.return_value = MagicMock()

        with patch.dict(sys.modules, {
            "firebase_admin": mock_firebase_admin,
            "firebase_admin.credentials": MagicMock(),
            "firebase_admin.firestore": mock_firestore_module,
        }):
            fs.initialise_firebase()

        s = fs.firebase_status()
        assert s["firebase_enabled"] is True
        assert s["firebase_connected"] is True
        assert s["firebase_project"] == "veksha-test-project"
        assert s["readings_collection"] == "health_readings"
        assert s["sos_collection"] == "sos_events"


# ---------------------------------------------------------------------------
# 4. End-to-end: main.py endpoints survive Firebase failures
# ---------------------------------------------------------------------------

class TestMainEndpointsWithFirebaseDown:
    """
    Ensures all existing API endpoints work correctly even when Firebase
    is completely unavailable (no credentials, SDK error, network failure).
    firebase_service is patched to simulate a total failure.
    """

    @pytest.fixture(autouse=True)
    def setup_client(self, monkeypatch):
        monkeypatch.setenv("FIREBASE_ENABLED", "false")
        monkeypatch.setenv("SOS_COOLDOWN_SECONDS", "0")
        monkeypatch.setenv("SMS_ENABLED", "false")
        import firebase_service as _fs
        _fs._firebase_enabled = False
        _fs._db = None
        _fs._init_error = "Disabled for test"

        import main as _main
        _main.firebase_service = _fs
        self.main = _main
        self.main.latest_reading = None
        self.main.recent_readings.clear()
        self.main.sos_events.clear()
        self.main._last_manual_sos_timestamp = 0.0
        self.client = TestClient(_main.app)

    def test_health_endpoint_survives(self):
        r = self.client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "online"
        assert "firebase" in r.json()

    def test_submit_reading_survives_firebase_failure(self):
        payload = {"heart_rate": 78.5, "spo2": 98.0, "sensor_status": "VALID_DATA"}
        r = self.client.post("/api/v1/readings", json=payload)
        assert r.status_code == 201
        assert r.json()["status"] == "success"
        # firestore_id must NOT be present when Firebase is down
        assert "firestore_id" not in r.json()["data"]

    def test_submit_reading_stored_in_memory_when_firebase_down(self):
        payload = {"heart_rate": 60.0, "spo2": 97.0, "sensor_status": "VALID_DATA"}
        self.client.post("/api/v1/readings", json=payload)
        r = self.client.get("/api/v1/readings/latest")
        assert r.status_code == 200
        assert r.json()["data"]["heart_rate"] == 60.0

    def test_trigger_sos_survives_firebase_failure(self):
        r = self.client.post("/api/v1/sos", json={})
        assert r.status_code == 201
        assert r.json()["status"] == "sos_triggered"
        assert "firestore_id" not in r.json()["data"]

    def test_sos_history_survives_firebase_failure(self):
        self.client.post("/api/v1/sos", json={"notes": "test"})
        r = self.client.get("/api/v1/sos/history")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_all_validation_still_works(self):
        """Pydantic validation must remain intact regardless of Firebase state."""
        r = self.client.post("/api/v1/readings", json={
            "heart_rate": None, "spo2": None, "sensor_status": "VALID_DATA"
        })
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 5. Firestore Read Query & JSON Credential Tests
# ---------------------------------------------------------------------------

class TestFirestoreQueriesAndCloudConfig:

    def test_json_string_credential_loading(self, monkeypatch):
        """FIREBASE_SERVICE_ACCOUNT_JSON raw JSON string should be accepted for cloud deployments."""
        monkeypatch.setenv("FIREBASE_ENABLED", "true")
        monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"project_id": "veksha-cloud-test"}')
        monkeypatch.setenv("FIREBASE_PROJECT_ID", "veksha-cloud-test")

        fs = _reload_firebase_service()
        mock_admin = MagicMock()
        mock_admin._apps = {}
        mock_creds = MagicMock()
        mock_fs = MagicMock()
        mock_admin.credentials = mock_creds
        mock_admin.firestore = mock_fs

        with patch.dict(sys.modules, {
            "firebase_admin": mock_admin,
            "firebase_admin.credentials": mock_creds,
            "firebase_admin.firestore": mock_fs,
        }):
            fs.initialise_firebase()

        mock_creds.Certificate.assert_called_once_with({"project_id": "veksha-cloud-test"})

    def test_get_recent_readings_returns_records(self, monkeypatch):
        """get_recent_readings_from_firestore maps document dicts properly."""
        fs = _reload_firebase_service()
        mock_db = MagicMock()
        mock_doc1 = MagicMock()
        mock_doc1.id = "doc1"
        mock_doc1.to_dict.return_value = {"heart_rate": 75.0, "spo2": 98.0, "timestamp": "2026-11-05T10:00:00Z"}

        mock_db.collection.return_value.order_by.return_value.limit.return_value.get.return_value = [mock_doc1]
        fs._db = mock_db
        fs._firebase_enabled = True

        res = fs.get_recent_readings_from_firestore(limit=10)
        assert len(res) == 1
        assert res[0]["firestore_id"] == "doc1"
        assert res[0]["heart_rate"] == 75.0
