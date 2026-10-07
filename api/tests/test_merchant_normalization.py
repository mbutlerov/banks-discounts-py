"""Relational catalogue projections use an explicitly isolated PostgreSQL schema."""
import os
from datetime import date
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import Bank, Category, MerchantGroup, MerchantLocation as StoredLocation, Promotion, PromotionOffer, PromotionOverride
from app.database.models.merchant_membership import MerchantAlias, MerchantLocationAlias, MerchantOfferRule, OfferLocation
from app.promotions.merchants import backfill_merchants, normalized_grouping
from app.promotions.schemas import Benefit, Eligibility, Evidence, MerchantIdentity, MerchantLocation, OfferData, Schedule
from app.scraping.persistence import save_promotions
from app.scraping.schemas import ScrapedPromotion


@pytest.fixture
def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    if "test" not in (make_url(url).database or "").lower():
        raise RuntimeError("Merchant tests require an explicitly named test database")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        schema = f"merchant_test_{uuid4().hex}"
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        Base.metadata.create_all(connection)
        with Session(bind=connection) as session:
            session.add_all([Bank(slug="ueno", name="Ueno"), Bank(slug="atlas", name="Atlas"), Category(slug="other", name="Otros")])
            session.commit()
            yield session
        transaction.rollback()
    engine.dispose()


def offer(key="branch-1", name="Superseis Centro", *, city="Asunción", address="Palma 100", channel="POS", explicit=True, location=True, **updates):
    evidence = Evidence(source_url="https://bank.example/conditions.pdf", text=f"{name} | {address} | {city}", page=2)
    fields = dict(key=key, merchant_name=name,
                  merchant=MerchantIdentity(namespace="chain", key="superseis", name="Superseis", evidence=[evidence]) if explicit else None,
                  valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 31), validity_state="known",
                  schedule=Schedule(state="known", kind="weekly", weekdays=[6]),
                  benefits=[Benefit(type="cashback", percentage=20)], publication="confirmed",
                  eligibility=Eligibility(cards=["Visa"], levels=["Nivel 3"], channels=[channel],
                                          cities=[city] if location else [], locations=[address] if location and address else []),
                  evidence=[evidence], terms=["Tope mensual por tarjeta."], source_url="https://bank.example/promotion",
                  locations=[MerchantLocation(key=key, name=name, city=city, address=address, channels=[channel], evidence=[evidence])] if location else [])
    fields.update(updates)
    return OfferData(**fields)


def scraped(*offers, bank="ueno", source="campaign:merchant:superseis", title="Superseis", **metadata):
    return ScrapedPromotion(bank_slug=bank, title=title, source_key=source,
                            metadata={"source_url": "https://bank.example/promotion", **metadata}, offers=list(offers))


def test_two_locations_share_one_rule_and_keep_original_rows_and_evidence(db):
    first, second = offer(), offer(key="branch-2", name="Superseis Luque", city="Luque", address="Ruta 1")
    save_promotions(db, [scraped(first, second)])
    parent = db.query(Promotion).one()
    source_identity = (parent.id, parent.slug)
    original = {row.id: (row.key, row.data_jsonb) for row in parent.offers}
    assert db.query(MerchantGroup).count() == db.query(MerchantOfferRule).count() == 1
    assert db.query(StoredLocation).count() == db.query(OfferLocation).count() == 2
    assert {row.location_scope for row in parent.offers} == {"specified"}
    assert db.query(MerchantOfferRule).one().data_jsonb["benefits"][0]["percentage"] == "20"
    assert "cities" not in db.query(MerchantOfferRule).one().data_jsonb["eligibility"]
    assert "evidence" not in db.query(MerchantOfferRule).one().data_jsonb
    assert backfill_merchants(db)["changes"] == {}
    assert (parent.id, parent.slug) == source_identity
    assert {row.id: (row.key, row.data_jsonb) for row in parent.offers} == original
    assert all(row.data_jsonb["locations"][0]["evidence"][0]["page"] == 2 for row in parent.offers)


def test_source_backed_explicit_chain_can_span_banks_with_independent_rules(db):
    save_promotions(db, [scraped(offer()), scraped(offer(), bank="atlas")])
    assert db.query(MerchantGroup).count() == 1
    assert db.query(StoredLocation).count() == 1
    assert db.query(MerchantOfferRule).count() == 2
    assert len({row.bank_id for row in db.query(MerchantOfferRule)}) == 2
    assert len({normalized_grouping(row.promotion, row).key for row in db.query(PromotionOffer)}) == 2


def test_name_only_catalogue_does_not_guess_crossbank_or_name_prefix_aliases(db):
    save_promotions(db, [scraped(offer(explicit=False)), scraped(offer(explicit=False), bank="atlas"),
                         scraped(offer(key="other", name="Superseis Luque", explicit=False), source="another-campaign")])
    assert db.query(MerchantGroup).count() == 3
    assert {row.namespace for row in db.query(MerchantAlias)} == {
        "bank:ueno:merchants", "bank:atlas:merchants", "bank:ueno:offer-merchants", "bank:atlas:offer-merchants"}


def test_same_physical_station_keeps_pos_and_app_rules_separate(db):
    save_promotions(db, [scraped(offer(key="pos"), offer(key="app", channel="App"))])
    assert db.query(StoredLocation).count() == 1
    assert db.query(MerchantOfferRule).count() == 2
    assert db.query(OfferLocation).count() == 2
    assert {tuple(row.data_jsonb["eligibility"]["channels"]) for row in db.query(MerchantOfferRule)} == {("POS",), ("App",)}
    assert db.query(MerchantLocationAlias).count() == 2


def test_local_correction_creates_distinct_rule_without_changing_source_identity(db):
    save_promotions(db, [scraped(offer(), offer(key="branch-2", name="Superseis Luque", city="Luque", address="Ruta 1"))])
    parent = db.query(Promotion).one()
    identifiers = {row.key: row.id for row in parent.offers}
    original_json = {row.id: row.data_jsonb.copy() for row in parent.offers}
    db.add(PromotionOverride(promotion_id=parent.id, offer_key="branch-2", reason="Bank confirmed branch-specific conditions",
                             patch_jsonb={"benefits": [{"type": "cashback", "percentage": "30"}], "valid_from": "2026-10-02"}))
    db.commit()
    report = backfill_merchants(db)
    assert report["changes"]["rules_created"] == 1
    assert db.query(MerchantOfferRule).count() == 2
    assert {row.key: row.id for row in parent.offers} == identifiers
    assert {row.id: row.data_jsonb for row in parent.offers} == original_json
    assert len({normalized_grouping(parent, row).key for row in parent.offers}) == 1
    assert backfill_merchants(db)["changes"] == {}


def test_location_alias_preserves_identity_when_official_branch_name_changes(db):
    save_promotions(db, [scraped(offer())])
    original_id = db.query(StoredLocation).one().id
    save_promotions(db, [scraped(offer(name="Superseis Centro renovado"))])
    assert db.query(StoredLocation).count() == 1
    assert db.query(StoredLocation).one().id == original_id
    assert db.query(StoredLocation).one().name == "Superseis Centro renovado"
    assert backfill_merchants(db)["changes"] == {}
    # A second bank's verified catalogue recognizes the renamed physical branch.
    save_promotions(db, [scraped(offer(key="atlas-official", name="Superseis Centro renovado"), bank="atlas")])
    assert db.query(StoredLocation).count() == 1


def test_stable_source_offer_keeps_merchant_identity_when_title_changes(db):
    save_promotions(db, [scraped(offer(explicit=False))])
    group_id = db.query(PromotionOffer).one().merchant_group_id
    save_promotions(db, [scraped(offer(name="Superseis nuevo nombre", explicit=False))])
    assert db.query(MerchantGroup).count() == 1
    assert db.query(PromotionOffer).one().merchant_group_id == group_id
    assert db.query(MerchantAlias).filter_by(namespace="bank:ueno:merchants").count() == 2


def test_no_address_never_merges_unidentified_branches_by_city_and_name(db):
    save_promotions(db, [scraped(offer(key="unknown-a", address=None), offer(key="unknown-b", address=None))])
    assert db.query(StoredLocation).count() == 2
    assert db.query(MerchantOfferRule).count() == 1


@pytest.mark.parametrize("scope", ["unknown", "all"])
def test_empty_location_list_requires_explicit_universal_scope(db, scope):
    save_promotions(db, [scraped(offer(location=False, location_scope=scope))])
    assert db.query(PromotionOffer).one().location_scope == scope
    assert db.query(OfferLocation).count() == 0
    assert backfill_merchants(db)["unknown_locations"] == (scope == "unknown")


def test_removed_source_offer_retires_adhesion_without_deleting_history(db):
    save_promotions(db, [scraped(offer())])
    original_offer = db.query(PromotionOffer).one()
    original_location = db.query(OfferLocation).one().location_id
    save_promotions(db, [scraped(offer(key="replacement", location=False), offers_complete="true")])
    db.refresh(original_offer)
    assert original_offer.publication == "retired"
    assert db.query(OfferLocation).filter_by(offer_id=original_offer.id, location_id=original_location).one().publication == "retired"
    assert db.query(StoredLocation).count() == 1
    assert original_offer.data_jsonb["locations"][0]["name"] == "Superseis Centro"


def test_backfill_dry_run_can_roll_back_every_catalogue_projection(db):
    save_promotions(db, [scraped(offer())])
    row = db.query(PromotionOffer).one()
    with db.begin_nested() as transaction:
        row.merchant_group_id = None
        row.rule_id = None
        db.flush()
        report = backfill_merchants(db)
        assert report["changes"]["offers_updated"] == 1
        transaction.rollback()
    assert db.query(PromotionOffer).one().merchant_group_id is not None
    assert backfill_merchants(db)["changes"] == {}


def test_manual_payment_correction_applies_to_rules_and_memberships(db):
    save_promotions(db, [scraped(offer())])
    parent = db.query(Promotion).one()
    db.add(PromotionOverride(promotion_id=parent.id, offer_key="branch-1", reason="Only corrected channel",
                             patch_jsonb={"eligibility": {"channels": ["QR"], "processors": ["Bancard"]}}))
    db.commit()
    backfill_merchants(db)
    row = db.query(PromotionOffer).one()
    assert row.rule.data_jsonb["eligibility"]["channels"] == ["QR"]
    assert row.rule.data_jsonb["eligibility"]["processors"] == ["Bancard"]
    assert row.location_memberships[0].data_jsonb["channels"] == ["QR"]
    assert row.location_memberships[0].data_jsonb["processors"] == ["Bancard"]
    assert row.data_jsonb["eligibility"]["channels"] == ["POS"]


def test_explicit_payment_patch_equal_to_raw_value_still_overrides_annex_route(db):
    # An old source observation advertised both introductory routes, while the
    # verified annex row narrowed this specific branch to POS.
    source = offer(eligibility=Eligibility(channels=["POS", "App"]))
    save_promotions(db, [scraped(source)])
    parent = db.query(Promotion).one()
    db.add(PromotionOverride(promotion_id=parent.id, offer_key="branch-1", reason="Bank confirmed both routes for this branch",
                             patch_jsonb={"eligibility": {"channels": ["POS", "App"]}}))
    db.commit()
    backfill_merchants(db)
    assert db.query(OfferLocation).one().data_jsonb["channels"] == ["POS", "App"]
    assert backfill_merchants(db)["changes"] == {}


@pytest.mark.parametrize("address", ["New address", "Palma 100"])
def test_manual_address_correction_withdraws_unverified_old_membership(db, address):
    save_promotions(db, [scraped(offer())])
    parent = db.query(Promotion).one()
    db.add(PromotionOverride(promotion_id=parent.id, offer_key="branch-1", reason="Old annex not verified",
                             patch_jsonb={"eligibility": {"locations": [address]}}))
    db.commit()
    backfill_merchants(db)
    row = db.query(PromotionOffer).one()
    assert row.location_scope == "unknown"
    assert row.location_memberships[0].publication == "retired"
    assert row.data_jsonb["locations"][0]["address"] == "Palma 100"
    assert backfill_merchants(db)["changes"] == {}
