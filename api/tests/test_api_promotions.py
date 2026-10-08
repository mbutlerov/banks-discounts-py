import os
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.v1.routes.catalogs import router as catalogs_router
from app.api.v1.routes.promotions import benefit_matches, router
from app.database.base import Base
from app.database.models import Bank, OfferOccurrence, Promotion, PromotionOffer, PromotionOverride, ScrapeRun
from app.database.session import get_db
from app.promotions.availability import regenerate_occurrences
from app.promotions.schemas import Benefit, Cap, Eligibility, Evidence, MerchantLocation, OfferData, Schedule


@pytest.fixture
def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    parsed = make_url(url)
    if "test" not in (parsed.database or "").lower():
        raise RuntimeError("Integration tests require an explicitly named test database")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
    engine.dispose()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(catalogs_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client


def create_offer(db, key="standard", weekdays=None, percentage=20, kind="cashback", merchant="Cafe", card="Visa", bank_slug="ueno", **changes):
    bank = db.query(Bank).filter_by(slug=bank_slug).first()
    if bank is None:
        bank = Bank(slug=bank_slug, name=bank_slug.title())
        db.add(bank)
        db.flush()
    parent = db.query(Promotion).filter_by(slug=f"campaign-{bank_slug}").first()
    if parent is None:
        parent = Promotion(bank=bank, slug=f"campaign-{bank_slug}", title="Campaign", status="draft", publication="confirmed")
        db.add(parent)
        db.flush()
    fields = dict(key=key, merchant_name=merchant, valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 31), validity_state="known", schedule=Schedule(state="known", kind="weekly", weekdays=weekdays or [6]), benefits=[Benefit(type=kind, percentage=percentage)], eligibility=Eligibility(cards=[card]), publication="confirmed")
    fields.update(changes)
    data = OfferData(**fields)
    row = PromotionOffer(promotion_id=parent.id, key=key, data_jsonb=data.model_dump(mode="json"), publication=data.publication, version=1)
    db.add(row)
    db.flush()
    return parent, row, data


def test_benefit_type_and_minimum_must_match_same_benefit():
    offer = OfferData(key="mixed", merchant_name="Cafe", benefits=[Benefit(type="discount", percentage=30), Benefit(type="cashback", percentage=10)])
    assert not benefit_matches(offer, "cashback", Decimal("30"), None)
    assert benefit_matches(offer, "discount", Decimal("30"), None)


def test_variant_filters_never_mix_card_percentage_and_day(client, db):
    create_offer(db, key="visa-saturday", weekdays=[6], percentage=10, card="Visa")
    create_offer(db, key="mastercard-sunday", weekdays=[7], percentage=30, card="Mastercard")
    response = client.get("/api/v1/promotions", params={"date_from": "2026-10-03", "date_to": "2026-10-04", "card": "Visa", "min_discount": 30})
    assert response.status_code == 200
    assert response.json()["total"] == 0
    response = client.get("/api/v1/promotions", params={"date": "2026-10-04", "card": "Mastercard", "min_discount": 30})
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["variants"][0]["key"] == "mastercard-sunday"


def test_grouping_count_and_pagination_after_calendar(client, db):
    create_offer(db, key="cafe-basic", merchant="Cafe", weekdays=[6])
    create_offer(db, key="cafe-gold", merchant="Cafe", weekdays=[6], percentage=30)
    create_offer(db, key="bakery", merchant="Bakery", weekdays=[6])
    create_offer(db, key="sunday-only", merchant="Sunday only", weekdays=[7])
    first = client.get("/api/v1/promotions", params={"date": "2026-10-03", "size": 1}).json()
    second = client.get("/api/v1/promotions", params={"date": "2026-10-03", "size": 1, "page": 2}).json()
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["merchant_name"] == "Bakery"
    assert len(second["items"][0]["variants"]) == 2
    assert second["items"][0]["matched_dates"] == ["2026-10-03"]


def test_paginated_groups_match_full_result_with_overrides_pending_and_chains(client, db):
    create_offer(db, key="cafe-basic", merchant="Cafe", weekdays=[6])
    create_offer(db, key="cafe-gold", merchant="Cafe", weekdays=[6], percentage=30)
    create_offer(db, key="bakery", merchant="Bakery", weekdays=[6])
    corrected, _, original = create_offer(db, key="corrected", merchant="Corrected", weekdays=[7])
    db.add(PromotionOverride(
        promotion_id=corrected.id,
        offer_key=original.key,
        patch_jsonb={"schedule": {"weekdays": [6]}},
        reason="Verified purchase-day correction",
    ))
    create_offer(db, key="pending", merchant="Pending", publication="pending", schedule=Schedule(state="unknown"))
    create_offer(db, key="atlas-coffee", merchant="Coffee", weekdays=[6], bank_slug="atlas")
    create_station(db, "petropar-terminal", "PETROPAR TERMINAL")
    create_station(db, "petropar-luque", "PETROPAR LUQUE", city="Luque", address="Avda. Pinasco")
    db.flush()

    params = {"date": "2026-10-03", "include_pending": "true"}
    complete = client.get("/api/v1/promotions", params={**params, "size": 100}).json()
    page_size = 2
    paged_items = []
    page_count = (complete["total"] + page_size - 1) // page_size
    for page in range(1, page_count + 1):
        response = client.get("/api/v1/promotions", params={**params, "size": page_size, "page": page}).json()
        assert response["total"] == complete["total"]
        paged_items.extend(response["items"])

    beyond_last = client.get(
        "/api/v1/promotions",
        params={**params, "size": page_size, "page": page_count + 1},
    ).json()
    assert beyond_last["total"] == complete["total"]
    assert beyond_last["items"] == []
    assert paged_items == complete["items"]
    assert next(item for item in paged_items if item["merchant_name"] == "Cafe")["variants"]
    assert next(item for item in paged_items if item["merchant_name"] == "Pending")["availability"] == "unknown"
    chain = next(item for item in paged_items if item["grouping"])
    assert chain["merchant_name"] == "Petropar"
    assert len(chain["locations"]) == 2


def test_unknown_visible_only_when_requested(client, db):
    create_offer(db, publication="pending", schedule=Schedule(state="unknown"))
    assert client.get("/api/v1/promotions", params={"date": "2026-10-03"}).json()["total"] == 0
    result = client.get("/api/v1/promotions", params={"date": "2026-10-03", "include_pending": "true"}).json()
    assert result["total"] == 1
    assert result["items"][0]["availability"] == "unknown"
    assert result["items"][0]["matched_dates"] == []


def test_future_promotions_ignore_legacy_draft_status(client, db):
    create_offer(db, valid_from=date(2026, 10, 10))
    assert client.get("/api/v1/promotions", params={"date": "2026-10-03"}).json()["total"] == 0
    assert client.get("/api/v1/promotions", params={"date": "2026-10-10"}).json()["total"] == 1


def test_date_query_validation(client):
    for params in ({"date": "bad"}, {"date": "2026-10-03", "date_from": "2026-10-03"}, {"date_from": "2026-10-04", "date_to": "2026-10-03"}, {"date_from": "2026-10-01", "date_to": "2026-11-01"}, {"min_discount": 50, "max_discount": 20}, {"page": 0}):
        assert client.get("/api/v1/promotions", params=params).status_code == 422


def test_multiple_banks_and_detail(client, db):
    create_offer(db, bank_slug="itau")
    create_offer(db, bank_slug="atlas")
    create_offer(db, bank_slug="gnb")
    response = client.get("/api/v1/promotions", params=[("date", "2026-10-03"), ("bank_slug", "itau"), ("bank_slug", "atlas")]).json()
    assert response["total"] == 2
    assert {item["bank"]["slug"] for item in response["items"]} == {"itau", "atlas"}
    detail = client.get("/api/v1/promotions/campaign-atlas", params={"date": "2026-10-03"}).json()
    assert detail["variants"][0]["matched_dates"] == ["2026-10-03"]
    assert client.get("/api/v1/promotions/missing").status_code == 404


def test_manual_override_changes_query_without_rewriting_source(client, db):
    parent, row, original = create_offer(db, weekdays=[7])
    db.add(PromotionOverride(promotion_id=parent.id, offer_key=original.key, patch_jsonb={"schedule": {"weekdays": [6]}}, reason="Verified purchase-day correction"))
    db.flush()
    assert client.get("/api/v1/promotions", params={"date": "2026-10-03"}).json()["total"] == 1
    assert row.data_jsonb["schedule"]["weekdays"] == [7]


def test_legacy_conflict_and_malformed_metadata_remain_unconfirmed(client, db):
    bank = Bank(slug="legacy-bank", name="Legacy Bank")
    db.add(bank)
    db.flush()
    db.add(Promotion(bank_id=bank.id, slug="legacy-conflict", title="Legacy", status="active", start_date=date(2026, 10, 31), end_date=date(2026, 10, 1), metadata_jsonb={"source_url": 42, "installments": "invalid", "applicable_cards": ["Visa"]}))
    db.flush()
    response = client.get("/api/v1/promotions", params={"date": "2026-10-03", "include_pending": "true"})
    assert response.status_code == 200
    row = next(item for item in response.json()["items"] if item["slug"] == "legacy-conflict")
    assert row["availability"] == "conflict"
    assert row["matched_dates"] == []
    assert row["source_url"] is None


def test_occurrence_regeneration_replaces_old_rules_and_rolls_back(db):
    _, row, original = create_offer(db, weekdays=[6])
    regenerate_occurrences(db, row, original, date(2026, 10, 1), horizon=31)
    assert date(2026, 10, 3) in list(db.scalars(select(OfferOccurrence.applies_on).where(OfferOccurrence.offer_id == row.id)))
    changed = original.model_copy(update={"schedule": Schedule(state="known", kind="weekly", weekdays=[7])})
    row.version += 1
    regenerate_occurrences(db, row, changed, date(2026, 10, 1), horizon=31)
    actual = list(db.scalars(select(OfferOccurrence.applies_on).where(OfferOccurrence.offer_id == row.id)))
    assert date(2026, 10, 3) not in actual
    assert date(2026, 10, 4) in actual
    versions = set(db.scalars(select(OfferOccurrence.rules_version).where(OfferOccurrence.offer_id == row.id)))
    assert versions == {2}
    with pytest.raises(ValueError):
        regenerate_occurrences(db, row, changed, date(2026, 10, 1), horizon=0)
    assert list(db.scalars(select(OfferOccurrence.applies_on).where(OfferOccurrence.offer_id == row.id))) == actual


def test_query_falls_back_when_occurrence_version_or_coverage_is_stale(client, db):
    _, row, original = create_offer(db, weekdays=[6])
    regenerate_occurrences(db, row, original, date(2026, 10, 1), horizon=7)
    assert client.get("/api/v1/promotions", params={"date": "2026-10-03"}).json()["total"] == 1
    assert client.get("/api/v1/promotions", params={"date": "2026-10-10"}).json()["total"] == 1
    changed = original.model_copy(update={"schedule": Schedule(state="known", kind="weekly", weekdays=[7])})
    row.version += 1
    row.data_jsonb = changed.model_dump(mode="json")
    db.flush()
    assert client.get("/api/v1/promotions", params={"date": "2026-10-03"}).json()["total"] == 0
    assert client.get("/api/v1/promotions", params={"date": "2026-10-04"}).json()["total"] == 1


def test_bank_catalog_reports_completed_health_without_exposing_internal_details(client, db):
    from datetime import datetime, timezone
    first = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    recent = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    db.add_all([
        Bank(slug="ueno", name="Ueno"), Bank(slug="atlas", name="Atlas"),
        Bank(slug="itau", name="Itaú"), Bank(slug="gnb", name="GNB"), Bank(slug="sudameris", name="Sudameris"),
        Bank(slug="inactive", name="Inactive", is_active=False),
        ScrapeRun(bank_slug="ueno", state="success", finished_at=first),
        ScrapeRun(bank_slug="ueno", state="failed", finished_at=recent, errors_jsonb=[{"message": "Private provider error", "url": "https://internal.example"}]),
        ScrapeRun(bank_slug="ueno", state="running", started_at=now),
        ScrapeRun(bank_slug="atlas", state="success", finished_at=first),
        ScrapeRun(bank_slug="atlas", state="partial", finished_at=recent),
        ScrapeRun(bank_slug="atlas", state="queued", started_at=now),
        ScrapeRun(bank_slug="itau", state="success", finished_at=recent),
        ScrapeRun(bank_slug="gnb", state="running", started_at=now),
        ScrapeRun(bank_slug="sudameris", state="failed", finished_at=recent),
        ScrapeRun(bank_slug="inactive", state="success", finished_at=now),
    ])
    db.flush()
    response = client.get("/api/v1/catalogs/banks")
    assert response.status_code == 200
    rows = {item["slug"]: item for item in response.json()}
    assert set(rows) == {"ueno", "atlas", "itau", "gnb", "sudameris"}
    assert rows["ueno"]["data_status"] == "unavailable"
    assert datetime.fromisoformat(rows["ueno"]["last_attempt_at"]) == recent
    assert datetime.fromisoformat(rows["ueno"]["last_updated_at"]) == first
    assert rows["atlas"]["data_status"] == "partial"
    assert datetime.fromisoformat(rows["atlas"]["last_updated_at"]) == recent
    assert rows["itau"]["data_status"] == "updated"
    assert rows["gnb"]["data_status"] == "never"
    assert rows["gnb"]["last_attempt_at"] is rows["gnb"]["last_updated_at"] is None
    assert rows["sudameris"]["data_status"] == "unavailable"
    assert rows["sudameris"]["last_updated_at"] is None
    assert "Private provider error" not in response.text
    assert "internal.example" not in response.text
    assert all(set(item) == {"slug", "name", "data_status", "last_attempt_at", "last_updated_at"} for item in rows.values())


def create_station(db, slug, name, city="Asunción", address="Avda. Uno", app=False, structured=False, bank_slug="ueno", pdf="https://www.ueno.com.py/petropar-oct.pdf", **changes):
    bank = db.query(Bank).filter_by(slug=bank_slug).first()
    if bank is None:
        bank = Bank(slug=bank_slug, name=bank_slug.title())
        db.add(bank)
        db.flush()
    parent = Promotion(bank=bank, slug=slug, title=name, publication="confirmed", status="active",
                       metadata_jsonb={"source_url": "https://www.ueno.com.py/beneficio-byc/oct2026/petropar/"})
    db.add(parent)
    db.flush()
    annex = Evidence(source_url=pdf, page=3, section="merchant-annex", method="pdf-table",
                     text=f"1 | {name} | {address} | {city}" + ("" if app else " | upay"))
    evidence = [Evidence(source_url=pdf, page=1, section="benefit-table", method="pdf-table", text="Nivel 5 | 40% | 600.000 | 240.000"), annex]
    fields = dict(key=slug + "-level5", merchant_name=name, source_url=pdf, valid_from=date(2026, 10, 1),
                  valid_until=date(2026, 10, 31), validity_state="known", schedule=Schedule(state="known", kind="all_days"),
                  benefits=[Benefit(type="cashback", percentage=40)],
                  eligibility=Eligibility(cards=["Mastercard Black"], levels=["Nivel 5"], cities=[city], locations=[address],
                                          channels=["app", "POS"], processors=["Upay"]),
                  caps=[Cap(type="cashback", amount=240000, period="campaign", scope="customer")],
                  terms=["Bases y Condiciones. Beneficio Reintegro Bolsa Petropar"], publication="confirmed", evidence=evidence)
    if structured:
        fields["locations"] = [MerchantLocation(key=slug + "-location", name=name, address=address, city=city,
                                               channels=["App Petropar"] if app else ["POS"],
                                               processors=[] if app else ["upay"], evidence=[annex])]
    fields.update(changes)
    offer = OfferData(**fields)
    db.add(PromotionOffer(promotion_id=parent.id, key=offer.key, publication=offer.publication, version=1,
                          data_jsonb=offer.model_dump(mode="json")))
    db.flush()
    return parent, offer


def test_petropar_chain_groups_before_pagination_and_preserves_old_station_urls(client, db):
    first, _ = create_station(db, "petropar-terminal", "PETROPAR TERMINAL")
    second, _ = create_station(db, "petropar-luque", "PETROPAR LUQUE", city="Luque", address="Avda. Pinasco")
    create_station(db, "airport-station", "SILVIO PETTIROSSI", city="Luque", address="Autopista", structured=True)
    create_offer(db, merchant="Cafe", weekdays=[1])
    result = client.get("/api/v1/promotions", params={"date": "2026-10-05", "size": 1}).json()
    assert result["total"] == 2
    assert result["items"][0]["merchant_name"] == "Cafe"
    chain = client.get("/api/v1/promotions", params={"date": "2026-10-05", "size": 1, "page": 2}).json()["items"][0]
    assert chain["title"] == chain["merchant_name"] == "Petropar"
    assert chain["grouping"]["kind"] == "chain"
    assert len(chain["locations"]) == 3
    assert len(chain["variants"]) == 1
    assert {item["city"] for item in chain["locations"]} == {"Asunción", "Luque"}
    assert all(item["variant_keys"] == [chain["variants"][0]["key"]] for item in chain["locations"])
    assert chain["variants"][0]["eligibility"]["locations"] == []
    assert all(item["source_page"] == 3 for item in chain["locations"])
    searched = client.get("/api/v1/promotions", params={"date": "2026-10-05", "search": "petropar"}).json()
    assert searched["total"] == 1
    assert len(searched["items"][0]["locations"]) == 3
    for parent in [first, second]:
        detail = client.get(f"/api/v1/promotions/{parent.slug}", params={"date": "2026-10-05"}).json()
        assert detail["title"] == "Petropar"
        assert len(detail["locations"]) == 3
        assert detail["variants"] == chain["variants"]
    assert db.query(Promotion).count() == 4
    assert db.query(PromotionOffer).count() == 4


def test_chain_search_matches_city_address_without_hiding_detail_siblings(client, db):
    create_station(db, "terminal", "PETROPAR TERMINAL")
    create_station(db, "luque", "PETROPAR LUQUE", city="Luque", address="Avda. Puerto Pinasco")
    for search in ["luque", "Pinasco", "asuncion"]:
        result = client.get("/api/v1/promotions", params={"date": "2026-10-05", "search": search}).json()
        assert result["total"] == 1
        assert len(result["items"][0]["locations"]) == 1
        detail = client.get("/api/v1/promotions/" + result["items"][0]["slug"], params={"date": "2026-10-05"}).json()
        assert len(detail["locations"]) == 2


def test_chain_dedup_keeps_caps_channels_cards_and_levels_separate(client, db):
    create_station(db, "pos", "PETROPAR POS")
    create_station(db, "app", "PETROPAR APP", app=True, address="Avda. Dos")
    create_station(db, "different-cap", "PETROPAR OTRO", address="Avda. Tres",
                   caps=[Cap(type="cashback", amount=100000, period="campaign", scope="customer")])
    create_station(db, "different-card", "PETROPAR CARD", address="Avda. Cuatro",
                   eligibility=Eligibility(cards=["Mastercard Clásica"], levels=["Nivel 4"], cities=["Asunción"], locations=["Avda. Cuatro"], channels=["POS"]))
    chain = client.get("/api/v1/promotions", params={"date": "2026-10-05"}).json()["items"][0]
    assert len(chain["variants"]) == 4
    by_name = {item["name"]: item for item in chain["locations"]}
    variants = {item["key"]: item for item in chain["variants"]}
    app = variants[by_name["PETROPAR APP"]["variant_keys"][0]]
    assert app["eligibility"]["channels"] == ["App Petropar"]
    assert app["eligibility"]["processors"] == []
    pos = variants[by_name["PETROPAR POS"]["variant_keys"][0]]
    assert pos["eligibility"]["channels"] == ["POS"]
    assert pos["eligibility"]["processors"] == ["upay"]
    assert pos["caps"][0]["amount"] == "240000"
    assert all(item["section"] != "merchant-annex" for item in pos["evidence"])
    assert all(item["evidence"][0]["section"] == "merchant-annex" for item in chain["locations"])
    result = client.get("/api/v1/promotions", params={"date": "2026-10-05", "channel": "App Petropar"}).json()
    assert result["total"] == 1
    assert len(result["items"][0]["locations"]) == len(result["items"][0]["variants"]) == 1


def test_chain_never_merges_banks_documents_validity_or_unverified_name_prefixes(client, db):
    create_station(db, "verified", "PETROPAR TERMINAL")
    create_station(db, "next-document", "PETROPAR OTRO", pdf="https://www.ueno.com.py/petropar-special.pdf")
    create_station(db, "different-validity", "PETROPAR OTRO PERIODO", valid_until=date(2026, 11, 30))
    create_station(db, "other-bank", "PETROPAR GNB", bank_slug="gnb")
    create_station(db, "unverified", "PETROPAR MARKETING", terms=["Hasta 40% con tu banco"])
    result = client.get("/api/v1/promotions", params={"date": "2026-10-05"}).json()
    assert result["total"] == 5
    assert len([item for item in result["items"] if item["grouping"]]) == 3
    detail = client.get("/api/v1/promotions/verified", params={"date": "2026-10-05"}).json()
    assert len(detail["locations"]) == 1
    assert detail["locations"][0]["name"] == "PETROPAR TERMINAL"


def test_chain_overrides_keep_corrected_campaign_detail_usable(client, db):
    parent, offer = create_station(db, "corrected", "PETROPAR TERMINAL")
    db.add(PromotionOverride(promotion_id=parent.id, offer_key=offer.key, patch_jsonb={"valid_until": "2026-11-30"}, reason="Source verified extension"))
    db.flush()
    detail = client.get("/api/v1/promotions/corrected", params={"date": "2026-11-05"}).json()
    assert len(detail["variants"]) == 1
    assert detail["variants"][0]["valid_until"] == "2026-11-30"
    assert detail["availability"] == "confirmed"


def test_same_station_pos_and_app_share_location_but_keep_separate_rules(client, db):
    create_station(db, "same-pos", "PETROPAR CENTRAL", address="Avda. Uno")
    create_station(db, "same-app", "PETROPAR CENTRAL", address="Avda. Uno", app=True)
    chain = client.get("/api/v1/promotions", params={"date": "2026-10-05"}).json()["items"][0]
    assert len(chain["locations"]) == 1
    assert len(chain["variants"]) == 2
    location = chain["locations"][0]
    assert set(location["channels"]) == {"App Petropar", "POS"}
    assert len(location["variant_keys"]) == len(location["evidence"]) == 2
    assert {tuple(item["eligibility"]["channels"]) for item in chain["variants"]} == {("POS",), ("App Petropar",)}
