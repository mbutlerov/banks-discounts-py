"""Persist/replay/admin regressions in an explicitly isolated PostgreSQL schema."""
import hashlib
import os
from datetime import date

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.database.models import Bank, Promotion, PromotionOffer, PromotionOverride, ScrapeRun, SourceDocument
from app.database.session import get_db
from app.promotions.schemas import Benefit, OfferData, Schedule
from app.scraping.clients.http_client import HttpClient, HttpClientRequestError, HttpResponseData, http_scope
from app.scraping.persistence import effective_offer, save_promotions
from app.scraping.registry import ADAPTER_VERSION
from app.scraping.schemas import ScrapedPromotion, ScrapedSource
from tests.helpers.ingestion import candidate, db, ingestion_engine


def test_idempotency_and_title_change_keep_source_identity_and_slug(db):
    assert save_promotions(db, [candidate()]) == (1, 0)
    original = db.query(Promotion).one()
    identifier, slug = original.id, original.slug
    assert save_promotions(db, [candidate(title="Cafe renamed")]) == (0, 1)
    current = db.query(Promotion).one()
    assert (current.id, current.slug) == (identifier, slug)
    assert current.title == "Cafe renamed"
    assert db.query(PromotionOffer).count() == 1


def test_absent_pdf_fields_preserve_previous_data(db):
    save_promotions(db, [candidate()])
    save_promotions(db, [candidate(description=None, discount_percentage=None, start_date=None, end_date=None, metadata={"source_url": "https://www.ueno.com.py/example", "applicable_cards": None}, offers=[])])
    row = db.query(Promotion).one()
    assert row.description == "Verified conditions"
    assert row.discount_percentage == 20
    assert row.end_date == date(2026, 10, 31)
    assert row.metadata_jsonb["applicable_cards"] == "Visa"
    assert db.query(PromotionOffer).count() == 1


def test_empty_extraction_cannot_erase_conditions(db):
    save_promotions(db, [candidate(terms_summary="Visa required")])
    save_promotions(db, [candidate(description="", terms_summary="", metadata={"applicable_cards": ""}, offers=[])])
    row = db.query(Promotion).one()
    assert row.description == "Verified conditions"
    assert row.terms_summary == "Visa required"
    assert row.metadata_jsonb["applicable_cards"] == "Visa"


def test_degraded_offer_keeps_evidence_and_fields_but_withdraws_confirmation(db):
    original = candidate()
    save_promotions(db, [original])
    weak = original.offers[0].model_copy(update={"publication": "pending", "benefits": [], "schedule": Schedule(state="unknown")})
    save_promotions(db, [candidate(offers=[weak])])
    row = db.query(PromotionOffer).one()
    assert row.publication == "pending"
    assert row.data_jsonb["benefits"][0]["percentage"] == "20"
    assert row.data_jsonb["schedule"]["weekdays"] == [6]
    assert row.version == 2
    assert db.query(Promotion).one().publication == "pending"


def test_distinct_merchants_and_variants_do_not_collapse(db):
    first = candidate()
    second_offer = first.offers[0].model_copy(update={"key": "other-merchant", "merchant_name": "Bakery", "schedule": Schedule(state="known", kind="weekly", weekdays=[7])})
    first.offers.append(second_offer)
    save_promotions(db, [first])
    assert db.query(Promotion).count() == 1
    assert db.query(PromotionOffer).count() == 2
    conditions = {row.data_jsonb["merchant_name"]: row.data_jsonb["schedule"]["weekdays"] for row in db.query(PromotionOffer)}
    assert conditions == {"Cafe": [6], "Bakery": [7]}


def test_scrape_preserves_manual_override_separate_from_original(db):
    save_promotions(db, [candidate()])
    parent = db.query(Promotion).one()
    db.add(PromotionOverride(promotion_id=parent.id, offer_key="standard", patch_jsonb={"schedule": {"weekdays": [7]}}, reason="Verified with source"))
    db.commit()
    save_promotions(db, [candidate()])
    stored = db.query(PromotionOffer).one()
    assert stored.data_jsonb["schedule"]["weekdays"] == [6]
    assert effective_offer(db, parent.id, OfferData.model_validate(stored.data_jsonb)).schedule.weekdays == [7]
    assert db.query(PromotionOverride).count() == 1


class NeverNetwork:
    def get(self, *args, **kwargs):
        raise AssertionError("Replay accessed the network")


def test_http_replay_is_offline_and_missing_document_fails():
    url = "https://www.ueno.com.py/example"
    data = HttpResponseData(url, 200, "fixture", b"fixture", {"Content-Type": "text/html"})
    client = HttpClient(session=NeverNetwork())
    with http_scope(replay={url: data}):
        assert client.get_text(url) == "fixture"
        with pytest.raises(HttpClientRequestError, match="ausente del replay"):
            client.get_text("https://www.ueno.com.py/missing")


@pytest.fixture
def runner_setup(ingestion_engine, monkeypatch, tmp_path):
    from app.scraping import runner
    monkeypatch.setattr(runner, "engine", ingestion_engine)
    monkeypatch.setattr(runner, "SessionLocal", sessionmaker(bind=ingestion_engine))
    monkeypatch.setattr(settings, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    return runner


def test_runner_listing_failure_is_failed_and_preserves_rows(runner_setup, db, monkeypatch):
    from app.scraping import factories
    save_promotions(db, [candidate()])

    class EmptySource:
        def fetch(self):
            return []

    monkeypatch.setattr(factories, "get_source", lambda bank: EmptySource())
    monkeypatch.setattr(factories, "get_parser", lambda bank: object())
    result = runner_setup.run_bank("ueno")
    assert result["state"] == "failed"
    assert result["counters"]["discovered"] == 0
    db.expire_all()
    assert db.query(Promotion).count() == 1
    assert db.query(Promotion).one().publication == "confirmed"


def test_runner_partial_snapshot_capture_and_offline_replay(runner_setup, db, monkeypatch):
    from app.scraping import factories
    from app.promotions.schemas import Evidence, MerchantLocation
    url = "https://www.ueno.com.py/example"
    response = HttpResponseData(url, 200, "fixture", b"fixture", {"Content-Type": "text/html"})

    class FixtureSource:
        def fetch(self):
            client = HttpClient(session=NeverNetwork())
            client.get(url)
            try:
                client.get("https://www.ueno.com.py/missing")
            except HttpClientRequestError:
                pass
            return [ScrapedSource(source_type="html", source_url=url, text="fixture")]

    class FixtureParser:
        def parse(self, source):
            scraped = candidate()
            scraped.offers[0].locations = [MerchantLocation(
                key="local-centro", name="Cafe Centro", city="Asunción", address="Palma 100",
                evidence=[Evidence(source_url=url, text="Cafe Centro | Palma 100 | Asunción", page=3)],
            )]
            return [scraped]

    monkeypatch.setattr(factories, "get_source", lambda bank: FixtureSource())
    monkeypatch.setattr(factories, "get_parser", lambda bank: FixtureParser())
    # Work queued by an older API records the adapter actually executing it.
    queued = ScrapeRun(bank_slug="ueno", state="queued", adapter_version="canonical-v1", pid=-1)
    db.add(queued)
    db.commit()
    result = runner_setup.run_bank("ueno", run_id=queued.id, replay={url: response})
    assert result["state"] == "partial"
    assert result["adapter_version"] == ADAPTER_VERSION
    assert result["counters"]["created"] == 1
    db.expire_all()
    completed = db.get(ScrapeRun, queued.id)
    assert completed.adapter_version == ADAPTER_VERSION
    assert completed.pid == os.getpid()
    document = db.query(SourceDocument).one()
    assert document.content_hash == hashlib.sha256(b"fixture").hexdigest()
    assert document.parser_version == ADAPTER_VERSION
    db.expire_all()
    location_evidence = db.query(PromotionOffer).one().data_jsonb["locations"][0]["evidence"][0]
    assert location_evidence["document_id"] == document.id
    assert location_evidence["page"] == 3
    bank, replay = runner_setup.load_replay(db, document.id)
    assert bank == "ueno"
    assert replay[url].content == b"fixture"
    repeated = runner_setup.run_bank(bank, replay=replay)
    assert repeated["state"] == "partial"
    assert repeated["adapter_version"] == ADAPTER_VERSION
    assert repeated["counters"]["updated"] == 1
    assert db.query(Promotion).count() == 1
    assert {row.parser_version for row in db.query(SourceDocument)} == {ADAPTER_VERSION}


def test_runner_rollback_retains_downloaded_source_for_investigation(runner_setup, db, monkeypatch):
    from app.scraping import factories
    url = "https://www.ueno.com.py/example"
    response = HttpResponseData(url, 200, "fixture", b"fixture", {"Content-Type": "text/html"})

    class CrashingSource:
        def fetch(self):
            HttpClient(session=NeverNetwork()).get(url)
            raise RuntimeError("Unexpected source format after download")

    monkeypatch.setattr(factories, "get_source", lambda bank: CrashingSource())
    result = runner_setup.run_bank("ueno", replay={url: response})
    assert result["state"] == "failed"
    document = db.query(SourceDocument).one()
    bank, replay = runner_setup.load_replay(db, document.id)
    assert bank == "ueno" and replay[url].content == b"fixture"
    assert document.run_id == result["id"]


def test_runner_bank_lock_rejects_second_run(runner_setup, ingestion_engine):
    bank = "ueno"
    key = int.from_bytes(hashlib.sha256(("banks-discounts:" + bank).encode()).digest()[:8], "big", signed=True)
    with ingestion_engine.connect() as other:
        assert other.execute(sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        try:
            with pytest.raises(runner_setup.RunBusy):
                runner_setup.run_bank(bank)
        finally:
            other.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def test_admin_legacy_get_authentication_and_durable_queue(db, monkeypatch):
    from app.api.v1.routes.scraping import legacy_router, router
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "test-admin-token")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(legacy_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert client.get("/api/v1/scraping/ueno/test").status_code == 410
        assert client.post("/api/v1/admin/scrape-runs", json={"bank_slug": "ueno"}).status_code == 401
        response = client.post("/api/v1/admin/scrape-runs", json={"bank_slug": "ueno"}, headers={"Authorization": "Bearer test-admin-token"})
        assert response.status_code == 202
        assert response.json()["state"] == "queued"
        assert response.json()["adapter_version"] == ADAPTER_VERSION
        identifier = response.json()["id"]
        assert db.get(ScrapeRun, identifier).state == "queued"
        assert db.get(ScrapeRun, identifier).adapter_version == ADAPTER_VERSION
        repeated = client.post("/api/v1/admin/scrape-runs", json={"bank_slug": "ueno"}, headers={"Authorization": "Bearer test-admin-token"})
        assert repeated.status_code == 409


def test_manual_correction_lifecycle_preserves_source_and_reports_real_changes(db, monkeypatch):
    from app.api.v1.routes.scraping import router as admin_router
    from app.api.v1.routes.promotions import router as public_router
    from app.promotions.schemas import Evidence

    scraped = candidate()
    scraped.offers[0].evidence = [Evidence(document_id=1, text="Bank conditions for Cafe")]
    save_promotions(db, [scraped])
    parent = db.query(Promotion).one()
    monkeypatch.setattr(settings, "ADMIN_API_KEY", "test-admin-token")
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(public_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    headers = {"Authorization": "Bearer test-admin-token"}
    endpoint = f"/api/v1/admin/promotions/{parent.id}/overrides"
    with TestClient(app) as client:
        invalid = client.post(endpoint, json={"offer_key": "standard", "patch": {"key": "wrong-identity"}, "reason": "Not allowed"}, headers=headers)
        assert invalid.status_code == 422
        assert db.query(PromotionOverride).count() == 0
        created = client.post(endpoint, json={"offer_key": "standard", "patch": {"schedule": {"weekdays": [7]}}, "reason": "Bank clarified Sunday"}, headers=headers)
        assert created.status_code == 200
        identifier = created.json()["id"]
        assert client.get("/api/v1/promotions", params={"date": "2026-10-03"}).json()["total"] == 0
        assert client.get("/api/v1/promotions", params={"date": "2026-10-04"}).json()["total"] == 1
        assert client.get(endpoint, headers=headers).json()[0]["source_changed"] is False
        # A new download of unchanged conditions changes its document ID only.
        scraped.offers[0].evidence = [Evidence(document_id=2, text="Bank conditions for Cafe")]
        save_promotions(db, [scraped])
        assert client.get(endpoint, headers=headers).json()[0]["source_changed"] is False
        # A changed benefit is meaningful and must remain visible for review.
        scraped.offers[0].benefits = [Benefit(type="cashback", percentage=30)]
        save_promotions(db, [scraped])
        assert client.get(endpoint, headers=headers).json()[0]["source_changed"] is True
        assert client.get("/api/v1/promotions", params={"date": "2026-10-04"}).json()["total"] == 1
        deleted = client.delete(f"/api/v1/admin/overrides/{identifier}", headers=headers)
        assert deleted.status_code == 200 and deleted.json()["active"] is False
        assert client.get("/api/v1/promotions", params={"date": "2026-10-03"}).json()["total"] == 1
        assert client.get("/api/v1/promotions", params={"date": "2026-10-04"}).json()["total"] == 0
    stored = db.query(PromotionOffer).one()
    assert stored.data_jsonb["schedule"]["weekdays"] == [6]
    assert stored.data_jsonb["benefits"][0]["percentage"] == "30"
    assert db.get(PromotionOverride, identifier).active is False


def test_recovery_respects_active_bank_lock_and_queues_once_even_after_recent_success(runner_setup, db, ingestion_engine):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    interrupted = ScrapeRun(bank_slug="ueno", state="running")
    db.add_all([ScrapeRun(bank_slug="ueno", state="success", finished_at=now), interrupted])
    db.commit()
    key = int.from_bytes(hashlib.sha256(b"banks-discounts:ueno").digest()[:8], "big", signed=True)
    with ingestion_engine.connect() as other:
        assert other.execute(sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        try:
            assert runner_setup.recover_interrupted_runs() == 0
            db.expire_all()
            assert db.get(ScrapeRun, interrupted.id).state == "running"
            assert db.query(ScrapeRun).filter_by(state="queued").count() == 0
        finally:
            other.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    assert runner_setup.recover_interrupted_runs() == 1
    db.expire_all()
    assert db.get(ScrapeRun, interrupted.id).state == "failed"
    assert db.get(ScrapeRun, interrupted.id).finished_at is not None
    assert db.query(ScrapeRun).filter_by(bank_slug="ueno", state="queued").count() == 1
    assert runner_setup.recover_interrupted_runs() == 0
    db.expire_all()
    assert db.query(ScrapeRun).filter_by(bank_slug="ueno", state="queued").count() == 1


def test_recovery_preserves_existing_queue_without_duplicate(runner_setup, db):
    pending = ScrapeRun(bank_slug="ueno", state="queued")
    interrupted = ScrapeRun(bank_slug="ueno", state="running")
    db.add_all([pending, interrupted])
    db.commit()
    assert runner_setup.recover_interrupted_runs() == 1
    db.expire_all()
    assert db.query(ScrapeRun).filter_by(bank_slug="ueno", state="queued").one().id == pending.id
    assert db.get(ScrapeRun, interrupted.id).state == "failed"


def test_schedule_due_uses_persisted_completion_time_across_worker_restarts(db, ingestion_engine, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.scraping import cli
    fixed = datetime(2026, 10, 3, 15, tzinfo=timezone.utc)

    class FixedDatetime:
        @staticmethod
        def now(tz=None):
            return fixed

    monkeypatch.setattr(cli, "datetime", FixedDatetime)
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=ingestion_engine))
    monkeypatch.setattr(settings, "SCRAPING_INTERVAL_SECONDS", 86400)
    assert cli.schedule_due("ueno") is True
    db.add(ScrapeRun(bank_slug="ueno", state="success", finished_at=fixed - timedelta(hours=1)))
    db.commit()
    assert cli.schedule_due("ueno") is False
    # Session factory recreation represents a restarted worker, without its timers.
    monkeypatch.setattr(cli, "SessionLocal", sessionmaker(bind=ingestion_engine))
    assert cli.schedule_due("ueno") is False
    fixed += timedelta(days=1)
    assert cli.schedule_due("ueno") is True


def test_worker_calendar_refreshes_daily_and_normalizes_bank_names(monkeypatch):
    import sys
    from app.scraping import cli
    clock = {"index": 0}
    times = [0, 60, 86401]
    refreshes, checked_banks, recoveries = [], [], []

    class StopWorker(Exception):
        pass

    def fake_sleep(_seconds):
        clock["index"] += 1
        if clock["index"] == len(times):
            raise StopWorker()

    def check_due(bank):
        checked_banks.append(bank)
        return False

    def forbidden_run(*args, **kwargs):
        raise AssertionError("A bank that is not due was scraped")

    monkeypatch.setattr(sys, "argv", ["scraping", "worker", "--schedule"])
    monkeypatch.setattr(settings, "SCRAPING_BANKS", "ueno, itau,")
    monkeypatch.setattr(settings, "SCRAPING_INTERVAL_SECONDS", 86400)
    monkeypatch.setattr(cli.time, "monotonic", lambda: times[clock["index"]])
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    monkeypatch.setattr(cli, "recover_interrupted_runs", lambda: recoveries.append(True) or 0)
    monkeypatch.setattr(cli, "process_queue", lambda: 0)
    monkeypatch.setattr(cli, "schedule_due", check_due)
    monkeypatch.setattr(cli, "run_bank", forbidden_run)
    monkeypatch.setattr(cli, "refresh_calendar", lambda: refreshes.append(times[clock["index"]]) or 0)
    with pytest.raises(StopWorker):
        cli.main()
    assert refreshes == [0, 86401]
    assert checked_banks == ["ueno", "itau"] * 3
    assert len(recoveries) == 1


def test_successful_reparse_clears_stale_observation_warnings_but_preserves_metadata(db):
    original = candidate(metadata={"source_url": "https://www.ueno.com.py/example", "parse_warning": "unrecognized_offer_variants",
                                   "document_error": "PDF unavailable", "discovery_warning": "fallback", "operator_note": "Reviewed identity"})
    original.offers[0].publication = "pending"
    save_promotions(db, [original])
    parent = db.query(Promotion).one()
    original_id = parent.id
    original_offer_id = db.query(PromotionOffer).one().id
    save_promotions(db, [candidate(metadata={"source_url": "https://www.ueno.com.py/example", "offers_complete": "true"})])
    db.refresh(parent)
    assert parent.id == original_id
    assert parent.publication == "confirmed"
    assert db.query(PromotionOffer).one().id == original_offer_id
    assert parent.metadata_jsonb["operator_note"] == "Reviewed identity"
    assert not any(k in parent.metadata_jsonb for k in ("parse_warning", "document_error", "discovery_warning"))
    save_promotions(db, [candidate(metadata={"parse_warning": "new_source_conflict"})])
    db.refresh(parent)
    assert parent.metadata_jsonb["parse_warning"] == "new_source_conflict"


def test_complete_offer_set_retires_old_variant_and_removes_its_occurrences(db):
    from app.database.models import OfferOccurrence
    from app.promotions.availability import regenerate_occurrences
    original = candidate()
    save_promotions(db, [original])
    parent = db.query(Promotion).one()
    old = db.query(PromotionOffer).one()
    original_id = old.id
    regenerate_occurrences(db, old, original.offers[0], date(2026, 10, 1), horizon=31)
    db.commit()
    assert db.query(OfferOccurrence).filter_by(offer_id=original_id).count() > 0
    replacement = candidate(offer_key="specific-variant", metadata={"offers_complete": "true", "source_url": "https://www.ueno.com.py/example"})
    assert save_promotions(db, [replacement]) == (0, 1)
    assert db.query(Promotion).one().id == parent.id
    db.refresh(old)
    assert old.publication == old.data_jsonb["publication"] == "retired"
    assert old.data_jsonb["benefits"] == original.offers[0].model_dump(mode="json")["benefits"]
    assert db.query(OfferOccurrence).filter_by(offer_id=original_id).count() == 0
    assert db.query(PromotionOffer).filter_by(key="specific-variant").one().publication == "confirmed"
    previous_version = old.version
    save_promotions(db, [replacement])
    db.refresh(old)
    assert old.version == previous_version
    assert db.query(PromotionOffer).count() == 2


@pytest.mark.parametrize("metadata", [{}, {"offers_complete": "true", "parse_warning": "Incomplete table"}, {"offers_complete": "true", "document_error": "PDF unavailable"}])
def test_partial_offer_set_preserves_missing_conditions_as_pending_without_changing_other_parent(db, metadata):
    from app.database.models import OfferOccurrence
    from app.promotions.availability import regenerate_occurrences
    first = candidate()
    unrelated = candidate(title="Unrelated commerce", key="merchant:2", offer_key="other-parent")
    save_promotions(db, [first, unrelated])
    parent = db.query(Promotion).filter_by(source_key="merchant:1").one()
    other_parent = db.query(Promotion).filter_by(source_key="merchant:2").one()
    old = db.query(PromotionOffer).filter_by(promotion_id=parent.id).one()
    regenerate_occurrences(db, old, first.offers[0], date(2026, 10, 1), horizon=31)
    db.commit()
    replacement = candidate(offer_key="new-variant", metadata=metadata)
    save_promotions(db, [replacement])
    db.refresh(old)
    assert old.publication == old.data_jsonb["publication"] == "pending"
    assert old.data_jsonb["benefits"] == first.offers[0].model_dump(mode="json")["benefits"]
    assert old.data_jsonb["schedule"]["weekdays"] == [6]
    assert db.query(OfferOccurrence).filter_by(offer_id=old.id).count() == 0
    other_offer = db.query(PromotionOffer).filter_by(promotion_id=other_parent.id).one()
    assert other_offer.publication == "confirmed"
    assert db.query(Promotion).count() == 2
    assert db.query(PromotionOffer).filter_by(promotion_id=parent.id).count() == 2


@pytest.mark.parametrize("complete", [True, False])
def test_source_merchant_reconciliation_is_scoped_to_accepted_document_groups(db, complete):
    from app.database.models import OfferOccurrence
    from app.promotions.availability import regenerate_occurrences
    from app.scraping.persistence import reconcile_source_merchants
    db.add(Bank(slug="atlas", name="Atlas"))
    db.commit()
    base, failed_base, unrelated_base = "document-a", "document-b", "document-c"
    original = candidate(title="Old group", key=f"{base}:merchant:general")
    failed_source = candidate(title="Failed source remains", key=f"{failed_base}:merchant:general")
    unrelated = candidate(title="Other document remains", key=f"{unrelated_base}:merchant:general")
    other_bank = candidate(title="Other bank remains", key=f"{base}:merchant:general", bank_slug="atlas")
    save_promotions(db, [original, failed_source, unrelated, other_bank])
    old_parent = db.query(Promotion).join(Bank).filter(Bank.slug == "ueno", Promotion.source_key == original.source_key).one()
    old_offer = db.query(PromotionOffer).filter_by(promotion_id=old_parent.id).one()
    regenerate_occurrences(db, old_offer, original.offers[0], date(2026, 10, 1), horizon=31)
    db.commit()
    assert db.query(OfferOccurrence).filter_by(offer_id=old_offer.id).count() > 0
    replacement = candidate(title="Concrete commerce", key=f"{base}:merchant:cafe", metadata={"offers_complete": "true"} if complete else {})
    rejected = candidate(title="Rejected extraction", key=f"{failed_base}:merchant:rejected", offers=[{"key": "malformed"}])
    save_promotions(db, [replacement], commit=False)
    # The runner only accepts candidates whose individual savepoint succeeds.
    with pytest.raises(ValueError):
        with db.begin_nested():
            save_promotions(db, [rejected], commit=False)
    changed = reconcile_source_merchants(db, [replacement, rejected], [replacement])
    db.commit()
    db.refresh(old_parent)
    db.refresh(old_offer)
    expected = "retired" if complete else "pending"
    assert changed == (1 if complete else 0)
    assert old_parent.publication == old_offer.publication == old_offer.data_jsonb["publication"] == expected
    assert old_offer.data_jsonb["benefits"] == original.offers[0].model_dump(mode="json")["benefits"]
    assert db.query(OfferOccurrence).filter_by(offer_id=old_offer.id).count() == 0
    for untouched in (failed_source, unrelated, other_bank):
        parent = db.query(Promotion).join(Bank).filter(Bank.slug == untouched.bank_slug, Promotion.source_key == untouched.source_key).one()
        assert parent.publication == "confirmed"
        assert db.query(PromotionOffer).filter_by(promotion_id=parent.id).one().publication == "confirmed"
    assert db.query(Promotion).filter_by(source_key=rejected.source_key).count() == 0
    assert db.query(Promotion).filter_by(source_key=replacement.source_key).one().publication == "confirmed"
