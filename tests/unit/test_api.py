"""Unit tests for the FastAPI application.

Runs without infrastructure: verifies the app starts cleanly with the
database down, the health endpoint reports degraded state honestly, and
data endpoints return an actionable 503 instead of a stack trace.
"""

import pytest
from fastapi.testclient import TestClient

from src.api import db
from src.api.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    def test_health_ok_without_database(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["database"] in ("connected", "unavailable")

    def test_openapi_docs_available(self, client):
        assert client.get("/openapi.json").status_code == 200


class TestGracefulDegradation:
    """Data endpoints must 503 with guidance when TimescaleDB is down."""

    @pytest.mark.parametrize("path", [
        "/api/v1/patients",
        "/api/v1/patients/PT-00001/vitals",
        "/api/v1/patients/PT-00001/mews",
        "/api/v1/patients/PT-00001/alerts",
        "/api/v1/units/ICU-2A/overview",
        "/api/v1/analytics/trends",
        "/api/v1/pipeline/status",
        "/api/v1/alerts",
    ])
    def test_data_endpoints_503_when_db_down(self, client, path):
        if db._pool is not None:
            pytest.skip("TimescaleDB is running; degradation not testable")
        resp = client.get(path)
        assert resp.status_code == 503
        assert "docker compose" in resp.json()["detail"]


class TestValidation:
    def test_invalid_interval_rejected(self, client):
        resp = client.get(
            "/api/v1/patients/PT-00001/vitals", params={"interval": "bogus"}
        )
        assert resp.status_code in (422, 503)

    def test_limit_bounds_enforced(self, client):
        resp = client.get(
            "/api/v1/patients/PT-00001/vitals", params={"limit": 999999}
        )
        assert resp.status_code == 422


class TestSeverityHelper:
    def test_severity_mapping(self):
        assert db._severity(0) == "normal"
        assert db._severity(2) == "normal"
        assert db._severity(3) == "warning"
        assert db._severity(4) == "warning"
        assert db._severity(5) == "critical"
        assert db._severity(12) == "critical"
        assert db._severity(None) == "unknown"
