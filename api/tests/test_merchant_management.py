"""Catalogue maintenance changes only the isolated test schema.

Exercise the actual CLI commit/rollback path rather than duplicating its
implementation. Source observations must survive every operation unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.database.base import Base
from app.database.models import (
    Bank, MerchantAlias, MerchantGroup, MerchantLocation, MerchantOfferRule,
    OfferLocation, Promotion, PromotionOffer,
)
from app.promotions.merchant_management import assign_merchant_alias, merchant_catalogue
from app.promotions.merchants import backfill_merchants
from app.promotions.schemas import Benefit, Eligibility, Evidence, MerchantLocation as LocationData, OfferData, Schedule
from app.scraping import cli


@pytest.fixture
def catalogue_db(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    if "test" not in (make_url(url).database or "").lower():
        raise RuntimeError("Catalogue tests require an explicitly named test database")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        schema = f"catalogue_test_{uuid4().hex}"
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        Base.metadata.create_all(connection)
        # PostgreSQL advisory locks are database-wide even when the tables live
        # in separate schemas. Preserve their semantics but namespace these test
        # keys, so another isolated suite cannot make an otherwise idle bank busy.
        lock_namespace = int.from_bytes(hashlib.sha256(schema.encode()).digest()[:8], "big") & ((1 << 63) - 1)

        class CatalogueSession(Session):
            def execute(self, statement, params=None, *args, **kwargs):
                if "pg_" in str(statement) and "advisory" in str(statement) and isinstance(params, dict) and "key" in params:
                    params = {**params, "key": params["key"] ^ lock_namespace}
                return super().execute(statement, params, *args, **kwargs)

        factory = sessionmaker(bind=connection, class_=CatalogueSession, join_transaction_mode="create_savepoint")
        monkeypatch.setattr(cli, "SessionLocal", factory)
        with factory() as db:
            db.info["test_lock_namespace"] = lock_namespace
            try:
                yield db, engine
            finally:
                db.close()
                transaction.rollback()
    engine.dispose()


def source_offer(db: Session, *, bank_slug="ueno", suffix="asuncion", merchant="Superseis", percentage=20):
    bank = db.query(Bank).filter_by(slug=bank_slug).first()
    if bank is None:
        bank = Bank(slug=bank_slug, name=bank_slug.title())
        db.add(bank)
        db.flush()
    parent = Promotion(
        bank=bank, slug=f"{bank_slug}-{suffix}", title=merchant,
        source_key=f"campaign:{bank_slug}:merchant:{suffix}",
        publication="confirmed", status="active",
    )
    db.add(parent)
    db.flush()
    source_url = f"https://{bank_slug}.example/beneficios/{suffix}"
    evidence = Evidence(source_url=source_url, text=f"{merchant}: {percentage}%", method="deterministic")
    data = OfferData(
        key=f"source-{suffix}", merchant_name=merchant,
        valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 31), validity_state="known",
        schedule=Schedule(state="known", kind="weekly", weekdays=[2]),
        benefits=[Benefit(type="cashback", percentage=percentage)],
        eligibility=Eligibility(cards=[f"{bank_slug} Visa"], channels=["POS"]),
        publication="confirmed", source_url=source_url, evidence=[evidence],
        locations=[LocationData(key=f"branch-{suffix}", name=f"{merchant} {suffix.title()}",
                                city=suffix.title(), address=f"Avenida {suffix} 123",
                                channels=["POS"], evidence=[evidence])],
    )
    stored = PromotionOffer(promotion=parent, key=data.key, source_key=f"stable:{suffix}",
                            data_jsonb=data.model_dump(mode="json"), publication="confirmed", version=3)
    db.add(stored)
    db.flush()
    return stored


def run_command(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["banks-discounts", *args])
    code = cli.main()
    return code, json.loads(capsys.readouterr().out)


def catalogue_counts(db):
    return [db.query(model).count() for model in (MerchantGroup, MerchantAlias, MerchantLocation, MerchantOfferRule, OfferLocation)]


def test_normalize_cli_defaults_to_dry_run_and_rolls_back(catalogue_db, monkeypatch, capsys):
    db, _ = catalogue_db
    row = source_offer(db)
    original = dict(row.data_jsonb)
    identifier = row.id
    db.commit()
    code, report = run_command(monkeypatch, capsys, "normalize-merchants")
    assert code == 0
    assert report["applied"] is False
    assert report["changes"]["merchants_created"] == 1
    assert report["changes"]["memberships_created"] == 1
    db.expire_all()
    assert catalogue_counts(db) == [0, 0, 0, 0, 0]
    preserved = db.get(PromotionOffer, identifier)
    assert (preserved.merchant_group_id, preserved.rule_id, preserved.location_scope) == (None, None, "unknown")
    assert preserved.data_jsonb == original
    assert preserved.version == 3


def test_normalize_cli_apply_is_idempotent_and_catalogue_join_works(catalogue_db, monkeypatch, capsys):
    db, _ = catalogue_db
    source_offer(db, suffix="asuncion")
    source_offer(db, suffix="luque")
    db.commit()
    code, report = run_command(monkeypatch, capsys, "normalize-merchants", "--apply")
    assert code == 0 and report["applied"]
    db.expire_all()
    assert catalogue_counts(db) == [1, 3, 2, 1, 2]
    db.commit()
    code, repeated = run_command(monkeypatch, capsys, "normalize-merchants", "--apply")
    assert code == 0 and repeated["applied"]
    assert repeated["changes"] == {}
    code, catalogue = run_command(monkeypatch, capsys, "merchant-list", "--search", "Superseis")
    assert code == 0
    assert catalogue["total"] == 1 and catalogue["unreconciled_offers"] == 0
    assert catalogue["items"][0]["banks"] == ["ueno"]
    assert catalogue["items"][0]["locations"] == 2
    assert {"namespace": "bank:ueno:merchants", "key": "superseis"} in catalogue["items"][0]["aliases"]
    assert len(catalogue["items"][0]["aliases"]) == 3


def test_reviewed_alias_cli_shares_merchant_preserves_bank_rules_and_manual_audit(catalogue_db, monkeypatch, capsys):
    db, _ = catalogue_db
    ueno = source_offer(db, bank_slug="ueno", suffix="asuncion", percentage=20)
    itau = source_offer(db, bank_slug="itau", suffix="luque", percentage=30)
    originals = {row.id: (row.key, row.data_jsonb, row.version, row.promotion.slug) for row in (ueno, itau)}
    backfill_merchants(db)
    target_slug = ueno.merchant_group.slug
    target_id = ueno.merchant_group_id
    db.commit()
    code, result = run_command(
        monkeypatch, capsys, "merchant-alias", "--namespace", "bank:itau:merchants", "--key", "superseis",
        "--merchant-slug", target_slug, "--reason", "Official merchant identity reviewed",
        "--source-url", "https://www.itau.com.py/beneficios", "--apply",
    )
    assert code == 0 and result["applied"]
    db.expire_all()
    assert {row.merchant_group_id for row in db.query(PromotionOffer)} == {target_id}
    for row in db.query(PromotionOffer):
        assert (row.key, row.data_jsonb, row.version, row.promotion.slug) == originals[row.id]
        assert row.rule.bank_id == row.promotion.bank_id
        assert row.rule.data_jsonb["eligibility"]["cards"] == [f"{row.promotion.bank.slug} Visa"]
        assert row.rule.data_jsonb["benefits"][0]["percentage"] == ("20" if row.promotion.bank.slug == "ueno" else "30")
        assert all(membership.location.merchant_group_id == target_id for membership in row.location_memberships if membership.publication != "retired")
    alias = db.query(MerchantAlias).filter_by(namespace="bank:itau:merchants", source_key="superseis").one()
    audit = dict(alias.evidence_jsonb)
    assert audit["method"] == "manual-review"
    assert audit["reason"] == "Official merchant identity reviewed"
    backfill_merchants(db)
    assert alias.evidence_jsonb == audit
    catalogue = merchant_catalogue(db, search="Superseis")
    target = next(item for item in catalogue["items"] if item["id"] == target_id)
    assert target["banks"] == ["itau", "ueno"]


def test_reviewed_alias_updates_previously_loaded_relationship(catalogue_db):
    db, _ = catalogue_db
    ueno = source_offer(db, bank_slug="ueno", suffix="asuncion")
    itau = source_offer(db, bank_slug="itau", suffix="luque")
    backfill_merchants(db)
    alias = db.query(MerchantAlias).filter_by(namespace="bank:itau:merchants").one()
    previous = alias.merchant_group
    target = ueno.merchant_group
    assert previous.id != target.id
    result = assign_merchant_alias(db, namespace=alias.namespace, key=alias.source_key,
                                   merchant_slug=target.slug, reason="Reviewed shared identity",
                                   source_url="https://www.itau.com.py/beneficios")
    assert not result["backfill"]["errors"]
    assert alias.merchant_group is target
    assert itau.merchant_group is target


def test_reviewed_alias_cli_defaults_to_dry_run(catalogue_db, monkeypatch, capsys):
    db, _ = catalogue_db
    ueno = source_offer(db, bank_slug="ueno", suffix="asuncion")
    itau = source_offer(db, bank_slug="itau", suffix="luque")
    backfill_merchants(db)
    alias = db.query(MerchantAlias).filter_by(namespace="bank:itau:merchants", source_key="superseis").one()
    previous_id, previous_evidence = alias.merchant_group_id, dict(alias.evidence_jsonb)
    target_slug = ueno.merchant_group.slug
    db.commit()
    code, result = run_command(
        monkeypatch, capsys, "merchant-alias", "--namespace", "bank:itau:merchants", "--key", "superseis",
        "--merchant-slug", target_slug, "--reason", "Reviewed shared identity",
        "--source-url", "https://www.itau.com.py/beneficios",
    )
    assert code == 0 and result["applied"] is False
    db.expire_all()
    assert alias.merchant_group_id == previous_id
    assert alias.evidence_jsonb == previous_evidence
    assert itau.merchant_group_id == previous_id


def test_normalize_cli_errors_abort_apply_and_rollback_partial_projection(catalogue_db, monkeypatch, capsys):
    db, _ = catalogue_db
    valid = source_offer(db, suffix="valid")
    invalid = source_offer(db, suffix="invalid")
    invalid.data_jsonb = {"key": "broken"}
    db.commit()
    code, result = run_command(monkeypatch, capsys, "normalize-merchants", "--apply")
    assert code == 1
    assert result["applied"] is False
    assert result["errors"][0]["offer_id"] == invalid.id
    assert result["changes"]["merchants_created"] == 1
    db.expire_all()
    assert catalogue_counts(db) == [0, 0, 0, 0, 0]
    assert db.get(PromotionOffer, valid.id).merchant_group_id is None


def test_busy_bank_lock_prevents_any_catalogue_mutation(catalogue_db, monkeypatch, capsys):
    db, engine = catalogue_db
    source_offer(db)
    db.commit()
    key = int.from_bytes(hashlib.sha256(b"banks-discounts:ueno").digest()[:8], "big", signed=True) ^ db.info["test_lock_namespace"]
    with engine.connect() as busy:
        with busy.begin():
            busy.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            monkeypatch.setattr(sys, "argv", ["banks-discounts", "normalize-merchants", "--apply"])
            with pytest.raises(SystemExit) as exc:
                cli.main()
            assert exc.value.code == 2
            assert "ueno" in capsys.readouterr().err
    db.expire_all()
    assert catalogue_counts(db) == [0, 0, 0, 0, 0]


def test_catalogue_search_treats_wildcards_as_literal_text(catalogue_db):
    db, _ = catalogue_db
    source_offer(db, merchant="100% Market", suffix="percent")
    source_offer(db, merchant="Market_One", suffix="underscore")
    backfill_merchants(db)
    assert merchant_catalogue(db, search="%")["total"] == 1
    assert merchant_catalogue(db, search="_")["total"] == 1
