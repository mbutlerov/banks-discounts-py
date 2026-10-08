"""The published schema describes real serialization and stays reproducible."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from scripts.contract_server import contract_app, require_test_database_url
from scripts.export_openapi import DEFAULT_OUTPUT, serialized_schema


def test_versioned_openapi_matches_application():
    assert DEFAULT_OUTPUT.read_text(encoding="utf-8") == serialized_schema()


def test_contract_server_rejects_working_database(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost/dev_banks_discounts")
    with pytest.raises(ValueError, match="test database"):
        require_test_database_url()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="Requires PostgreSQL test database")
def test_real_endpoints_return_variants_locations_and_pending():
    with TestClient(contract_app()) as client:
        response = client.get("/api/v1/promotions", params={"date": "2026-10-05"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        promotion = data["items"][0]
        assert promotion["merchant_name"] == "Contract Market"
        assert {location["city"] for location in promotion["locations"]} == {"Asunción", "Luque"}
        assert len(promotion["variants"]) == 2
        assert {float(v["benefits"][0]["percentage"]) for v in promotion["variants"]} == {20, 30}
        assert all(v["location_scope"] == "specified" for v in promotion["variants"])
        detail = client.get(f'/api/v1/promotions/{promotion["slug"]}', params={"date": "2026-10-05"})
        assert detail.status_code == 200
        assert detail.json()["locations"] == promotion["locations"]
        pending = client.get("/api/v1/promotions", params={"date": "2026-10-05", "include_pending": "true"})
        assert pending.json()["total"] == 2
        assert any(item["availability"] == "unknown" for item in pending.json()["items"])
        banks = client.get("/api/v1/catalogs/banks")
        assert banks.status_code == 200
        assert banks.json()[0]["data_status"] == "updated"
        assert client.get("/api/v1/catalogs/categories").json()[0]["slug"] == "supermarket"
        schema = client.get("/openapi.json").json()
        public_model = schema["components"]["schemas"]["PromotionResponse"]
        assert set(promotion) == set(public_model["required"])
        assert json.loads(serialized_schema()) == schema
