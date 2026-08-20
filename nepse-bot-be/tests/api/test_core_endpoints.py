"""
FastAPI endpoint tests for the platform-level endpoints (root, health,
config) and the v1 router aggregation.
"""

from __future__ import annotations

import pytest


class TestRootEndpoints:
    def test_root_returns_metadata(self, client):
        r = client.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["name"]
        assert body["version"]
        assert body["status"] == "running"
        assert "docs" in body
        assert "health" in body

    def test_docs_endpoint_serves_html(self, client):
        r = client.get("/docs")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_openapi_schema_exposed(self, client):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert "paths" in schema
        assert "/api/v1/recommendations/top" in schema["paths"]

    def test_config_endpoint(self, client):
        r = client.get("/config")
        assert r.status_code == 200
        body = r.json()
        assert body["app_name"]
        assert "intervals" in body


class TestHealthEndpoint:
    def test_health_returns_structured_response(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("healthy", "unhealthy")
        assert "checks" in body
        assert "database" in body["checks"]
        assert "api" in body["checks"]

    def test_db_info(self, client):
        r = client.get("/db-info")
        # Either the DB is reachable (200) or not (500) — both paths are
        # valid responses, but the shape must be consistent.
        assert r.status_code in (200, 500)
        body = r.json()
        assert "status" in body


class TestNotFound:
    def test_unknown_endpoint_returns_404(self, client):
        r = client.get("/does-not-exist-xyz")
        assert r.status_code == 404
