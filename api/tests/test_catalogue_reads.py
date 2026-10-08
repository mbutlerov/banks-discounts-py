"""Regression coverage for slim, batched promotion catalogue reads."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.v1.routes.promotions import router
from app.database.base import Base
from app.database.models import Bank, Category, Promotion, PromotionOffer, PromotionOverride
from app.database.models.merchant_group import MerchantGroup
from app.database.models.merchant_location import MerchantLocation as StoredLocation
from app.database.models.merchant_membership import MerchantOfferRule, OfferLocation
from app.database.session import get_db
from app.promotions.schemas import Benefit, Eligibility, MerchantLocation, OfferData, Schedule


@pytest.fixture
def catalogue_case():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    parsed = make_url(url)
    if "test" not in (parsed.database or "").lower():
        raise RuntimeError("Catalogue integration tests require an explicitly named test database")

    engine = create_engine(url)
    Base.metadata.create_all(engine)
    connection = engine.connect()
    transaction = connection.begin()
    setup = Session(bind=connection, join_transaction_mode="create_savepoint")
    case = {"engine": engine, "connection": connection, "db": setup, "suffix": uuid4().hex[:10]}
    try:
        yield case
    finally:
        setup.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.fixture
def catalogue_client(catalogue_case):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def fresh_session():
        # Each request gets a new identity map; tests should see database state,
        # not instances retained by the setup session or an earlier request.
        session = Session(bind=catalogue_case["connection"], join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = fresh_session
    with TestClient(app) as client:
        yield client


def _bank(db: Session, suffix: str) -> Bank:
    bank = Bank(slug=f"catalogue-{suffix}", name="Catalogue Test Bank")
    db.add(bank)
    db.flush()
    return bank


def _promotion(db: Session, bank: Bank, suffix: str, index: int, *, category: Category | None = None,
               legacy: bool = False) -> Promotion:
    return Promotion(
        bank_id=bank.id,
        category_id=category.id if category else None,
        slug=f"catalogue-{suffix}-{index}",
        title=f"Catalogue campaign {index}",
        short_description=f"Short description {index}",
        description=f"Full campaign description {index}",
        publication="pending" if legacy else "confirmed",
        status="active",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 31),
        benefit_type="installments" if legacy else "cashback",
        discount_percentage=None if legacy else 20,
        last_verified_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
        terms_summary=f"Terms summary {index}",
        metadata_jsonb={
            "source_url": f"https://source.test/{suffix}/{index}",
            "pdf_url": f"https://source.test/{suffix}/{index}.pdf",
            **({"applicable_cards": ["Visa"], "installments": "12"} if legacy else {}),
        },
    )


def _offer(db: Session, parent: Promotion, key: str, *, merchant: str | None = None,
           group: MerchantGroup | None = None, rule: MerchantOfferRule | None = None,
           location_scope: str = "unknown") -> PromotionOffer:
    data = OfferData(
        key=key,
        merchant_name=merchant or parent.title,
        source_url=f"https://source.test/offers/{key}",
        valid_from=date(2026, 10, 1),
        valid_until=date(2026, 10, 31),
        validity_state="known",
        schedule=Schedule(state="known", kind="all_days"),
        benefits=[Benefit(type="cashback", percentage=Decimal("20"))],
        eligibility=Eligibility(cards=["Visa"]),
        publication="confirmed",
        terms=[f"Source-backed terms for {key}"],
        location_scope=location_scope,
    )
    row = PromotionOffer(
        promotion_id=parent.id,
        key=key,
        publication="confirmed",
        version=1,
        merchant_group_id=group.id if group else None,
        rule_id=rule.id if rule else None,
        location_scope=location_scope,
        data_jsonb=data.model_dump(mode="json"),
    )
    db.add(row)
    db.flush()
    return row


def _selects_for_request(connection, client, params):
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        response = client.get("/api/v1/promotions", params=params)
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    return response.json(), statements


def test_catalogue_keeps_response_columns_and_normalized_location_overrides(catalogue_case, catalogue_client):
    db = catalogue_case["db"]
    suffix = catalogue_case["suffix"]
    bank = _bank(db, suffix)
    category = Category(slug=f"catalogue-category-{suffix}", name="Catalogue category")
    db.add(category)
    db.flush()
    parent = _promotion(db, bank, suffix, 1, category=category)
    db.add(parent)
    db.flush()

    group = MerchantGroup(slug=f"catalogue-merchant-{suffix}", name="Normalized Cafe")
    db.add(group)
    db.flush()
    rule = MerchantOfferRule(
        merchant_group_id=group.id,
        bank_id=bank.id,
        context_key=f"catalogue-context-{suffix}",
        fingerprint=f"catalogue-fingerprint-{suffix}",
        data_jsonb={"catalogue-test": True},
    )
    db.add(rule)
    db.flush()
    location = StoredLocation(
        merchant_group_id=group.id,
        identity_key=f"catalogue-location-{suffix}",
        name="Central branch",
        address="Main Avenue 100",
        city="Asuncion",
    )
    db.add(location)
    db.flush()
    offer = _offer(db, parent, "normalized-cafe", merchant="Cafe source label", group=group,
                   rule=rule, location_scope="specified")
    location_data = MerchantLocation(
        key="source-central",
        name="Central branch",
        address="Main Avenue 100",
        city="Asuncion",
        channels=["POS"],
        processors=["Bancard"],
    )
    db.add(OfferLocation(
        offer_id=offer.id,
        location_id=location.id,
        publication="confirmed",
        data_jsonb=location_data.model_dump(mode="json"),
    ))
    db.add(PromotionOverride(
        promotion_id=parent.id,
        offer_key=offer.key,
        patch_jsonb={"schedule": {"state": "known", "kind": "weekly", "weekdays": [7]}},
        reason="Catalogue regression override",
    ))
    db.flush()

    result = catalogue_client.get(
        "/api/v1/promotions", params={"date": "2026-10-04", "category_slug": category.slug},
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["short_description"] == "Short description 1"
    assert item["description"] == "Full campaign description 1"
    assert item["terms_summary"] == "Terms summary 1"
    assert item["pdf_url"] == f"https://source.test/{suffix}/1.pdf"
    assert item["source_url"] == f"https://source.test/{suffix}/1"
    assert item["last_checked_at"] == "2026-10-02T00:00:00Z"
    assert item["bank"] == {"slug": bank.slug, "name": bank.name}
    assert item["category"] == {"slug": category.slug, "name": category.name}
    assert item["grouping"]["key"].startswith(f"merchant-{group.id}-{bank.id}-")
    assert item["grouping"]["name"] == "Normalized Cafe"
    assert item["grouping"]["kind"] == "chain"
    assert item["locations"][0]["name"] == "Central branch"
    assert item["locations"][0]["city"] == "Asuncion"
    assert item["locations"][0]["address"] == "Main Avenue 100"
    assert item["locations"][0]["channels"] == ["POS"]
    assert item["variants"][0]["schedule"]["weekdays"] == [7]
    assert item["variants"][0]["matched_dates"] == ["2026-10-04"]


def test_legacy_pending_promotion_retains_cards_installments_and_metadata(catalogue_case, catalogue_client):
    db = catalogue_case["db"]
    suffix = catalogue_case["suffix"]
    bank = _bank(db, suffix)
    parent = _promotion(db, bank, suffix, 1, legacy=True)
    db.add(parent)
    db.flush()

    response = catalogue_client.get(
        "/api/v1/promotions",
        params={"date": "2026-10-04", "include_pending": "true", "card": "Visa", "benefit_type": "installments"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    item = payload["items"][0]
    variant = item["variants"][0]
    assert item["description"] == "Full campaign description 1"
    assert item["terms_summary"] == "Terms summary 1"
    assert item["pdf_url"] == f"https://source.test/{suffix}/1.pdf"
    assert item["source_url"] == f"https://source.test/{suffix}/1"
    assert item["last_checked_at"] == "2026-10-02T00:00:00Z"
    assert item["status"] == "pending"
    assert variant["publication"] == "pending"
    assert variant["eligibility"]["cards"] == ["Visa"]
    assert variant["benefits"][0]["type"] == "installments"
    assert variant["benefits"][0]["installments"] == 12


def test_page_query_count_is_batched_and_empty_page_skips_response_text_read(catalogue_case, catalogue_client):
    db = catalogue_case["db"]
    suffix = catalogue_case["suffix"]
    bank = _bank(db, suffix)
    for index in range(14):
        parent = _promotion(db, bank, suffix, index)
        db.add(parent)
        db.flush()
        _offer(db, parent, f"page-offer-{index}")
    db.flush()

    params = {"date": "2026-10-05"}
    small, small_selects = _selects_for_request(
        catalogue_case["connection"], catalogue_client, {**params, "page": 1, "size": 2},
    )
    large, large_selects = _selects_for_request(
        catalogue_case["connection"], catalogue_client, {**params, "page": 1, "size": 20},
    )
    assert small["total"] == large["total"] == 14
    assert len(small["items"]) == 2
    assert len(large["items"]) == 14
    # Fixed select-in/batch work may add a small constant, never one SELECT per
    # parent as the response page grows.
    assert len(large_selects) <= len(small_selects) + 2

    empty, empty_selects = _selects_for_request(
        catalogue_case["connection"], catalogue_client, {**params, "page": 99, "size": 20},
    )
    assert empty["items"] == []
    assert len(empty_selects) < len(large_selects)
    assert not any("description" in statement.lower() for statement in empty_selects)
