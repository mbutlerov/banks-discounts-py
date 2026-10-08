"""Normalized merchant identities preserve branch rules and old campaign URLs."""
import hashlib
import json
import os
from datetime import date
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.v1.routes.promotions import router
from app.database.base import Base
from app.database.models import Bank, MerchantGroup, Promotion, PromotionOffer, PromotionOverride
from app.database.models.merchant_location import MerchantLocation as StoredLocation
from app.database.models.merchant_membership import MerchantOfferRule, OfferLocation
from app.database.session import get_db
from app.promotions.schemas import Benefit, Cap, Eligibility, Evidence, MerchantLocation, OfferData, Schedule


@pytest.fixture
def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    if "test" not in (make_url(url).database or "").lower():
        raise RuntimeError("Integration tests require an explicitly named test database")
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        schema = "merchant_api_" + uuid4().hex
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        Base.metadata.create_all(connection)
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
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as result:
        yield result


def branch(db, slug, *, brand="Superseis", bank_slug="ueno", city="Asunción", address="Avda. España 100",
           context="monthly-supermarkets", percentage=20, publication="confirmed", scope="specified",
           pdf="https://bank.example/supermarkets.pdf", structured=True, **changes):
    bank = db.query(Bank).filter_by(slug=bank_slug).first()
    if bank is None:
        bank = Bank(slug=bank_slug, name=bank_slug.title())
        db.add(bank)
        db.flush()
    group = db.query(MerchantGroup).filter_by(slug=brand.casefold()).first()
    if group is None:
        group = MerchantGroup(slug=brand.casefold(), name=brand)
        db.add(group)
        db.flush()
    parent = Promotion(bank=bank, slug=slug, title=f"{brand} {city}", publication=publication,
                       source_key=f"{bank_slug}:monthly:merchant:{slug}",
                       metadata_jsonb={"source_url": "https://bank.example/supermarkets"})
    db.add(parent)
    db.flush()
    annex = Evidence(source_url=pdf, page=4, section="merchant-annex", text=f"{brand} | {address} | {city}")
    location = MerchantLocation(key=slug + "-branch", name=f"{brand} {city}", city=city, address=address,
                                channels=["POS"], processors=["Infonet"], evidence=[annex])
    fields = dict(key=slug + "-offer", merchant_name=f"{brand} {city}", valid_from=date(2026, 10, 1),
                  valid_until=date(2026, 10, 31), validity_state="known", publication=publication,
                  schedule=Schedule(state="known", kind="all_days"), benefits=[Benefit(type="cashback", percentage=percentage)],
                  eligibility=Eligibility(cards=["Visa"], cities=[city], locations=[address], channels=["POS"], processors=["Infonet"]),
                  caps=[Cap(amount=100000, period="month", scope="customer")],
                  source_url=pdf, location_scope=scope, locations=[location] if structured and scope == "specified" else [],
                  evidence=[Evidence(source_url=pdf, page=1, section="benefit-table", text="20% cashback"), annex])
    if scope != "specified":
        fields["eligibility"] = Eligibility(cards=["Visa"], channels=["POS"])
        fields["evidence"] = fields["evidence"][:1]
    fields.update(changes)
    data = OfferData(**fields)
    rule_data = data.model_dump(mode="json")
    for field in ("key", "merchant_name", "merchant", "locations"):
        rule_data.pop(field, None)
    rule_data["eligibility"].pop("cities", None)
    rule_data["eligibility"].pop("locations", None)
    rule_data["evidence"] = [item for item in rule_data["evidence"] if item.get("section") != "merchant-annex"]
    fingerprint = hashlib.sha256(json.dumps(rule_data, sort_keys=True).encode()).hexdigest()
    context_key = hashlib.sha256(context.encode()).hexdigest()
    rule = db.query(MerchantOfferRule).filter_by(merchant_group_id=group.id, bank_id=bank.id,
                                               context_key=context_key, fingerprint=fingerprint).first()
    if rule is None:
        rule = MerchantOfferRule(merchant_group_id=group.id, bank_id=bank.id, context_key=context_key,
                                 fingerprint=fingerprint, data_jsonb=rule_data)
        db.add(rule)
        db.flush()
    row = PromotionOffer(promotion_id=parent.id, merchant_group_id=group.id, rule_id=rule.id,
                         key=data.key, data_jsonb=data.model_dump(mode="json"), publication=publication, location_scope=scope)
    db.add(row)
    db.flush()
    if scope == "specified":
        stored = StoredLocation(merchant_group_id=group.id, name=location.name, city=city, address=address,
                                identity_key=hashlib.sha256(slug.encode()).hexdigest())
        db.add(stored)
        db.flush()
        db.add(OfferLocation(offer_id=row.id, location_id=stored.id, publication=publication,
                             data_jsonb=location.model_dump(mode="json")))
    db.flush()
    return parent, row, data


def listing(client, **params):
    response = client.get("/api/v1/promotions", params={"date": "2026-10-05", **params})
    assert response.status_code == 200, response.text
    return response.json()


def detail(client, slug, **params):
    response = client.get(f"/api/v1/promotions/{slug}", params={"date": "2026-10-05", **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_shared_merchant_groups_locations_before_counting_and_paging(client, db):
    first, _, _ = branch(db, "superseis-central")
    second, _, _ = branch(db, "superseis-luque", city="Luque", address="Ruta 2")
    branch(db, "petromax-central", brand="Petromax", context="fuel")
    result = listing(client, size=1)
    assert result["total"] == 2
    assert result["items"][0]["title"] == "Petromax"
    card = listing(client, size=1, page=2)["items"][0]
    assert card["title"] == card["merchant_name"] == "Superseis"
    assert len(card["locations"]) == 2
    assert len(card["variants"]) == 1
    assert card["location_scope"] == "specified"
    assert {location["city"] for location in card["locations"]} == {"Asunción", "Luque"}
    assert all(location["variant_keys"] == [card["variants"][0]["key"]] for location in card["locations"])
    for parent in (first, second):
        old_url = detail(client, parent.slug)
        assert old_url["title"] == "Superseis"
        assert old_url["variants"] == card["variants"]
        assert len(old_url["locations"]) == 2
    assert db.query(Promotion).count() == db.query(PromotionOffer).count() == 3


def test_city_address_search_keeps_complete_old_slug_detail(client, db):
    branch(db, "central")
    branch(db, "luque", city="Luque", address="Avda. Puerto Pinasco")
    for search in ("asuncion", "Luque", "Pinasco"):
        result = listing(client, search=search)
        assert result["total"] == 1
        assert len(result["items"][0]["locations"]) == 1
        assert len(detail(client, result["items"][0]["slug"])["locations"]) == 2


def test_other_banks_and_campaign_periods_keep_separate_cards(client, db):
    branch(db, "ueno-superseis")
    branch(db, "itau-superseis", bank_slug="itau")
    branch(db, "ueno-extra-superseis", context="special-weekend", percentage=30)
    result = listing(client)
    assert result["total"] == 3
    assert {item["bank"]["slug"] for item in result["items"]} == {"itau", "ueno"}
    assert len({item["grouping"]["key"] for item in result["items"]}) == 3
    assert len(detail(client, "ueno-superseis")["locations"]) == 1


def test_pdf_replacement_keeps_stable_campaign_group(client, db):
    branch(db, "central", pdf="https://bank.example/old.pdf")
    branch(db, "luque", city="Luque", address="Ruta 2", pdf="https://bank.example/replacement.pdf")
    result = listing(client)
    assert result["total"] == 1
    assert len(result["items"][0]["locations"]) == 2
    assert detail(client, "central")["grouping"]["key"] == detail(client, "luque")["grouping"]["key"]
    # Different source evidence remains available even when the display groups it.
    assert {variant["source_url"] for variant in result["items"][0]["variants"]} == {
        "https://bank.example/old.pdf", "https://bank.example/replacement.pdf"}


def test_local_override_preserves_other_branch_rules_and_source(client, db):
    parent, row, original = branch(db, "central")
    branch(db, "luque", city="Luque", address="Ruta 2")
    db.add(PromotionOverride(promotion_id=parent.id, offer_key=original.key,
                             patch_jsonb={"benefits": [{"type": "cashback", "percentage": "30"}],
                                          "eligibility": {"channels": ["App"]}}, reason="Verified central branch condition"))
    db.flush()
    card = listing(client)["items"][0]
    assert len(card["variants"]) == 2
    variants = {variant["key"]: variant for variant in card["variants"]}
    central = next(location for location in card["locations"] if location["city"] == "Asunción")
    luque = next(location for location in card["locations"] if location["city"] == "Luque")
    assert central["channels"] == ["App"]
    assert variants[central["variant_keys"][0]]["benefits"][0]["percentage"] == "30"
    assert variants[luque["variant_keys"][0]]["benefits"][0]["percentage"] == "20"
    assert variants[central["variant_keys"][0]]["eligibility"]["channels"] == ["App"]
    assert row.data_jsonb["eligibility"]["channels"] == ["POS"]
    assert row.data_jsonb["benefits"][0]["percentage"] == "20"
    app_only = listing(client, channel="App")["items"][0]
    assert len(app_only["locations"]) == 1


def test_retired_memberships_do_not_return_adhered_locations(client, db):
    _, row, _ = branch(db, "central")
    row.location_memberships[0].publication = "retired"
    db.flush()
    card = listing(client)["items"][0]
    assert card["locations"] == []
    assert card["location_scope"] == "specified"
    assert card["variants"][0]["eligibility"]["locations"] == ["Avda. España 100"]


def test_source_aliases_of_one_physical_location_use_stable_catalogue_id(client, db):
    _, first, _ = branch(db, "first-source")
    _, second, _ = branch(db, "second-source", city="ASUNCIÓN", address="AVDA. ESPAÑA 100")
    first_membership = first.location_memberships[0]
    second_membership = second.location_memberships[0]
    second_membership.location = first_membership.location
    db.flush()
    db.expire(first, ["location_memberships"])
    db.expire(second, ["location_memberships"])
    card = listing(client)["items"][0]
    assert len(card["locations"]) == 1
    assert card["locations"][0]["key"] == f"catalogue:location:{first_membership.location_id}"
    assert card["locations"][0]["city"] == "Asunción"
    assert len(card["locations"][0]["evidence"]) == 2


def test_retired_petropar_membership_cannot_reappear_from_legacy_pdf_annex(client, db):
    _, row, data = branch(db, "petropar", brand="Petropar", context="fuel",
                          pdf="https://www.ueno.com.py/petropar.pdf", terms=["Beneficio Reintegro Bolsa Petropar"])
    source = data.model_dump(mode="json")
    source["locations"] = []
    source["evidence"][-1].update(method="pdf-table", text="1 | PETROPAR CENTRAL | Avda. España 100 | Asunción | Infonet")
    row.data_jsonb = source
    row.location_memberships[0].publication = "retired"
    db.flush()
    card = listing(client)["items"][0]
    assert card["locations"] == []
    assert detail(client, "petropar")["locations"] == []


def test_backfilled_petropar_uses_exact_pos_app_annex_routes_before_filtering(client, db):
    _, pos, pos_data = branch(db, "petropar-pos", brand="Petropar", context="fuel",
                             pdf="https://www.ueno.com.py/petropar.pdf", terms=["Beneficio Reintegro Bolsa Petropar"])
    _, app, app_data = branch(db, "petropar-app", brand="Petropar", context="fuel", city="Luque", address="Ruta 2",
                             pdf="https://www.ueno.com.py/petropar.pdf", terms=["Beneficio Reintegro Bolsa Petropar"])
    for row, data, channels, processors in ((pos, pos_data, ["POS"], ["Upay"]),
                                            (app, app_data, ["App Petropar"], [])):
        source = data.model_dump(mode="json")
        source["eligibility"].update(channels=["app", "POS"], processors=["Upay"])
        row.data_jsonb = source
        membership = dict(row.location_memberships[0].data_jsonb)
        membership.update(channels=channels, processors=processors)
        row.location_memberships[0].data_jsonb = membership
    db.flush()
    card = listing(client)["items"][0]
    assert len(card["variants"]) == 2
    assert {tuple(variant["eligibility"]["channels"]) for variant in card["variants"]} == {("POS",), ("App Petropar",)}
    pos_card = listing(client, channel="POS")["items"][0]
    app_card = listing(client, channel="App Petropar")["items"][0]
    assert [location["city"] for location in pos_card["locations"]] == ["Asunción"]
    assert [location["city"] for location in app_card["locations"]] == ["Luque"]
    assert app_card["variants"][0]["eligibility"]["processors"] == []
    assert pos.data_jsonb["eligibility"]["channels"] == app.data_jsonb["eligibility"]["channels"] == ["app", "POS"]


def test_unknown_location_scope_never_claims_all_branches(client, db):
    branch(db, "central", scope="unknown")
    card = listing(client)["items"][0]
    assert card["location_scope"] == "unknown"
    assert card["locations"] == []
    assert card["variants"][0]["location_scope"] == "unknown"
    branch(db, "all-branches", brand="Petromax", context="fuel", scope="all")
    all_card = next(item for item in listing(client)["items"] if item["title"] == "Petromax")
    assert all_card["location_scope"] == "all"


def test_pending_branch_does_not_become_confirmed_through_shared_merchant(client, db):
    branch(db, "confirmed")
    branch(db, "pending", city="Luque", address="Ruta 2", publication="pending",
           schedule=Schedule(state="unknown"))
    card = listing(client)["items"][0]
    assert len(card["locations"]) == 1
    mixed = listing(client, include_pending="true")["items"][0]
    assert len(mixed["locations"]) == 2
    assert {variant["availability"] for variant in mixed["variants"]} == {"confirmed", "unknown"}


def test_merchant_correction_is_visible_before_reconciliation(client, db):
    parent, _, original = branch(db, "central")
    db.add(PromotionOverride(promotion_id=parent.id, offer_key=original.key,
                             patch_jsonb={"merchant_name": "Verified different merchant"}, reason="Source merchant corrected"))
    db.flush()
    card = listing(client)["items"][0]
    assert card["title"] == "Verified different merchant"
    assert card["grouping"] is None


def test_multi_merchant_campaign_detail_keeps_all_merchants(client, db):
    first, _, _ = branch(db, "one-campaign")
    _, second, _ = branch(db, "second-source", brand="Petromax", context="fuel")
    second.promotion_id = first.id
    db.flush()
    campaign = detail(client, first.slug)
    assert campaign["grouping"] is None
    assert {variant["merchant_name"].split()[0] for variant in campaign["variants"]} == {"Superseis", "Petromax"}
