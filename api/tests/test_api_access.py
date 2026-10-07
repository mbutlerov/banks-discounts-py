"""The hosted API is private even when requests never reach a route dependency."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.database.session import get_db
from app.main import create_app


READ_KEY = "test-read-access-key"
ADMIN_KEY = "test-administration-key"


@pytest.fixture
def protected_client(monkeypatch):
    monkeypatch.setattr(settings, "API_ACCESS_KEY", READ_KEY)
    monkeypatch.setattr(settings, "ADMIN_API_KEY", ADMIN_KEY)
    application = create_app()
    database_calls = []

    class HealthyDatabase:
        def execute(self, statement):
            database_calls.append(str(statement))

        def get(self, model, identifier):
            database_calls.append(f"GET {model.__name__} {identifier}")
            return None

    application.dependency_overrides[get_db] = lambda: HealthyDatabase()
    with TestClient(application) as client:
        yield client, database_calls


@pytest.mark.parametrize("path", [
    "/api/v1/promotions",
    "/api/v1/catalogs/banks",
    "/api/v1/health",
    "/api/v1/admin/scrape-runs",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/missing",
    "/healthz/",
])
def test_data_docs_unknown_paths_and_readiness_require_access_key(protected_client, path):
    client, database_calls = protected_client
    for headers in ({}, {"X-API-Key": "incorrect"}, {"Authorization": f"Bearer {READ_KEY}"}):
        response = client.get(path, headers=headers)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert READ_KEY not in response.text
        assert ADMIN_KEY not in response.text
    assert database_calls == []


def test_correct_key_reaches_protected_health_and_docs(protected_client):
    client, database_calls = protected_client
    health = client.get("/api/v1/health", headers={"X-API-Key": READ_KEY})
    assert health.status_code == 200
    assert health.json()["db"] == "ok"
    assert database_calls == ["SELECT 1"]
    assert client.get("/openapi.json", headers={"x-api-key": READ_KEY}).status_code == 200
    assert client.get("/docs", headers={"X-API-Key": READ_KEY}).status_code == 200
    assert client.get("/missing", headers={"X-API-Key": READ_KEY}).status_code == 404


@pytest.mark.parametrize("headers", [
    [(b"x-api-key", b"\xff")],
    [(b"x-api-key", "clave inválida".encode("utf-8"))],
    [(b"x-api-key", b"")],
    [(b"x-api-key", READ_KEY.encode()), (b"x-api-key", b"incorrect")],
    [(b"x-api-key", READ_KEY.encode()), (b"x-api-key", READ_KEY.encode())],
    [(b"x-api-key", f"{READ_KEY}, {READ_KEY}".encode())],
])
def test_malformed_and_duplicate_credentials_return_401(protected_client, headers):
    client, database_calls = protected_client
    assert client.get("/api/v1/health", headers=headers).status_code == 401
    assert database_calls == []


def test_liveness_is_public_and_never_uses_the_database(protected_client):
    client, database_calls = protected_client
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    head = client.head("/healthz")
    assert head.status_code == 200
    assert head.content == b""
    assert database_calls == []
    assert client.post("/healthz").status_code == 401
    assert "/healthz" not in client.get("/openapi.json", headers={"X-API-Key": READ_KEY}).json()["paths"]


def test_cors_preflight_does_not_bypass_access_protection(protected_client):
    client, database_calls = protected_client
    response = client.options("/api/v1/promotions", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "X-API-Key",
    })
    assert response.status_code == 401
    assert database_calls == []


def test_read_access_does_not_grant_admin_access(protected_client):
    client, database_calls = protected_client
    for authorization in (None, f"Bearer {READ_KEY}"):
        headers = {"X-API-Key": READ_KEY}
        if authorization:
            headers["Authorization"] = authorization
        response = client.get("/api/v1/admin/scrape-runs", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    assert database_calls == []


def test_independent_read_and_admin_keys_allow_admin_route(protected_client):
    client, database_calls = protected_client
    response = client.get("/api/v1/admin/scrape-runs/missing", headers={
        "X-API-Key": READ_KEY,
        "Authorization": f"Bearer {ADMIN_KEY}",
    })
    assert response.status_code == 404
    assert database_calls == ["GET ScrapeRun missing"]


@pytest.mark.parametrize("authorization", [
    [(b"authorization", b"Bearer \xff")],
    [(b"authorization", "Bearer contraseña".encode("utf-8"))],
    [(b"authorization", f"Bearer {ADMIN_KEY}".encode()), (b"authorization", f"Bearer {ADMIN_KEY}".encode())],
])
def test_invalid_admin_headers_return_401_with_valid_read_key(protected_client, authorization):
    client, database_calls = protected_client
    response = client.get("/api/v1/admin/scrape-runs", headers=[
        (b"x-api-key", READ_KEY.encode()), *authorization,
    ])
    assert response.status_code == 401
    assert database_calls == []


def test_disabled_admin_stays_disabled_with_valid_read_key(protected_client, monkeypatch):
    client, database_calls = protected_client
    monkeypatch.setattr(settings, "ADMIN_API_KEY", None)
    assert client.get("/api/v1/admin/scrape-runs", headers={"X-API-Key": READ_KEY}).status_code == 503
    assert database_calls == []


def test_unconfigured_key_preserves_local_access(monkeypatch):
    monkeypatch.setattr(settings, "API_ACCESS_KEY", None)
    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/missing").status_code == 404


def make_settings(**changes):
    return Settings(_env_file=None, DB_HOST="localhost", DB_PORT=5432, DB_NAME="test_access",
                    DB_USER="postgres", DB_PASSWORD="private-database-secret",
                    API_ACCESS_KEY=changes.pop("API_ACCESS_KEY", None),
                    REQUIRE_API_ACCESS_KEY=changes.pop("REQUIRE_API_ACCESS_KEY", False), **changes)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_required_access_key_cannot_be_missing_or_blank(key):
    with pytest.raises(ValidationError, match="REQUIRE_API_ACCESS_KEY requiere") as error:
        make_settings(API_ACCESS_KEY=key, REQUIRE_API_ACCESS_KEY=True)
    assert "private-database-secret" not in str(error.value)


@pytest.mark.parametrize("key", ["secret with spaces", "private-secret\n", "private-secret\t", "private-secret-é"])
def test_invalid_configured_keys_are_rejected_without_printing_them(key):
    with pytest.raises(ValidationError, match="API_ACCESS_KEY debe") as error:
        make_settings(API_ACCESS_KEY=key)
    assert key not in str(error.value)
    assert "private-database-secret" not in str(error.value)


def test_optional_and_required_valid_key_configuration():
    assert make_settings().API_ACCESS_KEY is None
    assert make_settings(API_ACCESS_KEY="   ").API_ACCESS_KEY is None
    assert make_settings(API_ACCESS_KEY=READ_KEY, REQUIRE_API_ACCESS_KEY=True).API_ACCESS_KEY == READ_KEY
