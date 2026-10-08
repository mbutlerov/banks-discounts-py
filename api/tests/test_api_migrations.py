"""Upgrade in isolated PostgreSQL schemas; never migrate the working database."""
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

API_DIR = Path(__file__).resolve().parents[1]
MEMBERSHIP_REVISION = "20261005_merchant_membership"
OFFERS_REVISION = "20261003_offers_and_ingestion"


@pytest.fixture
def migration_connection():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    if "test" not in (make_url(url).database or "").lower():
        raise RuntimeError("Migration tests require an explicitly named test database")
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        schema = f"migration_test_{uuid4().hex}"
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
        try:
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()


def config(connection):
    result = Config(str(API_DIR / "alembic.ini"))
    result.set_main_option("script_location", str(API_DIR / "alembic"))
    result.attributes["connection"] = connection
    return result


def build_unversioned_legacy(connection, include_discount=True):
    spec = importlib.util.spec_from_file_location("frozen_baseline", API_DIR / "alembic/versions/20260511_legacy_baseline.py")
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    metadata = sa.MetaData()
    for name, columns, indices in baseline.definitions():
        table = sa.Table(name, metadata, *columns)
        for column in indices:
            sa.Index(f"ix_{name}_{column}", table.c[column], unique=column == "slug")
    metadata.create_all(connection)
    if include_discount:
        connection.execute(sa.text("ALTER TABLE promotions ADD COLUMN discount_percentage INTEGER"))
    connection.execute(sa.text("INSERT INTO banks (id,slug,name,country_code,is_active) VALUES (1,'ueno','Ueno','PY',TRUE)"))
    connection.execute(sa.text("INSERT INTO promotions (id,bank_id,slug,title,status,applies_to_all_locations) VALUES (1,1,'preserved','Existing promotion','active',TRUE)"))


def assert_head(connection):
    tables = set(sa.inspect(connection).get_table_names())
    assert {"banks", "promotions", "promotion_offers", "offer_occurrences", "scrape_runs", "source_documents", "promotion_overrides", "alembic_version", "merchant_aliases", "merchant_location_aliases", "merchant_offer_rules", "offer_locations"} <= tables
    assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == MEMBERSHIP_REVISION
    columns = {column["name"] for column in sa.inspect(connection).get_columns("promotions")}
    assert {"source_key", "discount_percentage", "publication", "last_seen_at", "last_verified_at", "last_run_id"} <= columns
    offer_columns = {column["name"] for column in sa.inspect(connection).get_columns("promotion_offers")}
    assert {"merchant_group_id", "rule_id", "location_scope"} <= offer_columns
    location_columns = {column["name"] for column in sa.inspect(connection).get_columns("merchant_locations")}
    assert "identity_key" in location_columns


def test_empty_database_upgrade(migration_connection):
    command.upgrade(config(migration_connection), "head")
    assert_head(migration_connection)
    assert migration_connection.execute(sa.text("SELECT count(*) FROM promotions")).scalar_one() == 0


def test_adopt_unversioned_legacy_with_existing_discount(migration_connection):
    build_unversioned_legacy(migration_connection)
    command.upgrade(config(migration_connection), "head")
    assert_head(migration_connection)
    row = migration_connection.execute(sa.text("SELECT title,publication FROM promotions WHERE slug='preserved'")).one()
    assert row == ("Existing promotion", "pending")


def test_upgrade_existing_original_revision_preserves_data(migration_connection):
    build_unversioned_legacy(migration_connection)
    command.stamp(config(migration_connection), "b8a7ff77da5a")
    command.upgrade(config(migration_connection), "head")
    assert_head(migration_connection)
    assert migration_connection.execute(sa.text("SELECT title FROM promotions WHERE id=1")).scalar_one() == "Existing promotion"


def test_downgrade_cannot_destroy_adopted_legacy_data(migration_connection):
    build_unversioned_legacy(migration_connection)
    command.stamp(config(migration_connection), "b8a7ff77da5a")
    command.upgrade(config(migration_connection), "head")
    with pytest.raises(RuntimeError, match="adopted data"):
        with migration_connection.begin_nested():
            command.downgrade(config(migration_connection), "base")
    assert_head(migration_connection)
    assert migration_connection.execute(sa.text("SELECT title FROM promotions WHERE id=1")).scalar_one() == "Existing promotion"


def build_pre_membership_data(connection):
    build_unversioned_legacy(connection)
    command.stamp(config(connection), "b8a7ff77da5a")
    command.upgrade(config(connection), OFFERS_REVISION)
    connection.execute(sa.text("""
        INSERT INTO merchant_groups (id,slug,name,is_active)
        VALUES (1,'preserved-merchant','Original merchant',TRUE)
    """))
    connection.execute(sa.text("""
        INSERT INTO merchant_locations (id,merchant_group_id,name,address,city,is_active)
        VALUES (1,1,'Original location','Original address','Asuncion',TRUE)
    """))
    connection.execute(sa.text("""
        INSERT INTO promotion_offers (id,promotion_id,key,source_key,data_jsonb,version,publication)
        VALUES (1,1,'original-offer','source:stable',CAST(:data AS jsonb),7,'confirmed')
    """), {"data": '{"merchant_name":"Original merchant","source_url":"https://example.com/legal.pdf","locations":[{"key":"location-original"}]}'} )
    connection.execute(sa.text("""
        INSERT INTO promotion_overrides (id,promotion_id,offer_key,patch_jsonb,reason)
        VALUES (1,1,'original-offer','{"terms":"Manual correction"}','Preserve review')
    """))
    connection.execute(sa.text("""
        INSERT INTO offer_occurrences (offer_id,applies_on,rules_version)
        VALUES (1,'2026-10-05',7)
    """))


def add_membership_data(connection):
    connection.execute(sa.text("UPDATE merchant_locations SET identity_key='stable-location' WHERE id=1"))
    connection.execute(sa.text("""
        INSERT INTO merchant_aliases (id,namespace,source_key,merchant_group_id)
        VALUES (1,'ueno','original-merchant',1)
    """))
    connection.execute(sa.text("""
        INSERT INTO merchant_location_aliases (id,namespace,source_key,location_id)
        VALUES (1,'ueno','original-location',1)
    """))
    connection.execute(sa.text("""
        INSERT INTO merchant_offer_rules (id,merchant_group_id,bank_id,context_key,fingerprint,data_jsonb)
        VALUES (1,1,1,'campaign-stable','rules-stable',CAST(:data AS jsonb))
    """), {"data": '{"benefits":[{"percentage":20}]}'} )
    connection.execute(sa.text("""
        UPDATE promotion_offers SET merchant_group_id=1,rule_id=1,location_scope='specified' WHERE id=1
    """))
    connection.execute(sa.text("""
        INSERT INTO offer_locations (offer_id,location_id,data_jsonb)
        VALUES (1,1,'{"key":"source-row","channels":["POS"]}')
    """))


def assert_original_observation(connection):
    row = connection.execute(sa.text("""
        SELECT id,key,source_key,version,publication,data_jsonb->>'merchant_name'
        FROM promotion_offers WHERE id=1
    """)).one()
    assert row == (1, "original-offer", "source:stable", 7, "confirmed", "Original merchant")
    assert connection.execute(sa.text("SELECT slug FROM promotions WHERE id=1")).scalar_one() == "preserved"
    assert connection.execute(sa.text("SELECT offer_key,patch_jsonb->>'terms' FROM promotion_overrides WHERE id=1")).one() == ("original-offer", "Manual correction")
    assert connection.execute(sa.text("SELECT rules_version FROM offer_occurrences WHERE offer_id=1")).scalar_one() == 7


def test_membership_upgrade_preserves_source_observations_and_defaults_unknown(migration_connection):
    build_pre_membership_data(migration_connection)
    command.upgrade(config(migration_connection), "head")
    assert_head(migration_connection)
    assert_original_observation(migration_connection)
    assert migration_connection.execute(sa.text("SELECT merchant_group_id,rule_id,location_scope FROM promotion_offers WHERE id=1")).one() == (None, None, "unknown")
    assert migration_connection.execute(sa.text("SELECT name,address,identity_key FROM merchant_locations WHERE id=1")).one() == ("Original location", "Original address", None)
    for table in ("merchant_aliases", "merchant_location_aliases", "merchant_offer_rules", "offer_locations"):
        assert migration_connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
    command.upgrade(config(migration_connection), "head")
    assert_original_observation(migration_connection)


def test_membership_database_constraints(migration_connection):
    build_pre_membership_data(migration_connection)
    command.upgrade(config(migration_connection), "head")
    add_membership_data(migration_connection)
    invalid_statements = [
        "INSERT INTO merchant_aliases (id,namespace,source_key,merchant_group_id) VALUES (2,'ueno','original-merchant',1)",
        "INSERT INTO merchant_location_aliases (id,namespace,source_key,location_id) VALUES (2,'ueno','original-location',1)",
        "INSERT INTO merchant_locations (id,merchant_group_id,name,identity_key,is_active) VALUES (2,1,'Duplicate','stable-location',TRUE)",
        "INSERT INTO merchant_offer_rules (id,merchant_group_id,bank_id,context_key,fingerprint,data_jsonb) VALUES (2,1,1,'campaign-stable','rules-stable','{}')",
        "INSERT INTO offer_locations (offer_id,location_id,data_jsonb) VALUES (1,1,'{}')",
        "UPDATE promotion_offers SET location_scope='all-guessed' WHERE id=1",
        "UPDATE offer_locations SET publication='unknown' WHERE offer_id=1",
    ]
    for statement in invalid_statements:
        with pytest.raises(sa.exc.IntegrityError):
            with migration_connection.begin_nested():
                migration_connection.execute(sa.text(statement))
    assert migration_connection.execute(sa.text("SELECT publication FROM offer_locations WHERE offer_id=1")).scalar_one() == "confirmed"
    # Names alone are not identities; unresolved legacy locations remain valid.
    migration_connection.execute(sa.text("""
        INSERT INTO merchant_locations (id,merchant_group_id,name,is_active)
        VALUES (2,1,'Original location',TRUE),(3,1,'Original location',TRUE)
    """))
    assert_original_observation(migration_connection)


def test_membership_cascade_preserves_source_observation(migration_connection):
    build_pre_membership_data(migration_connection)
    command.upgrade(config(migration_connection), "head")
    add_membership_data(migration_connection)
    migration_connection.execute(sa.text("DELETE FROM merchant_groups WHERE id=1"))
    assert_original_observation(migration_connection)
    assert migration_connection.execute(sa.text("SELECT merchant_group_id,rule_id FROM promotion_offers WHERE id=1")).one() == (None, None)
    for table in ("merchant_locations", "merchant_aliases", "merchant_location_aliases", "merchant_offer_rules", "offer_locations"):
        assert migration_connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_membership_downgrade_preserves_previous_schema_and_observations(migration_connection):
    build_pre_membership_data(migration_connection)
    command.upgrade(config(migration_connection), "head")
    add_membership_data(migration_connection)
    command.downgrade(config(migration_connection), OFFERS_REVISION)
    assert_original_observation(migration_connection)
    assert migration_connection.execute(sa.text("SELECT name,address FROM merchant_locations WHERE id=1")).one() == ("Original location", "Original address")
    tables = set(sa.inspect(migration_connection).get_table_names())
    assert not {"merchant_aliases", "merchant_location_aliases", "merchant_offer_rules", "offer_locations"} & tables
    assert connection_revision(migration_connection) == OFFERS_REVISION


def connection_revision(connection):
    return connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


def test_membership_migration_matches_registered_models(migration_connection):
    command.upgrade(config(migration_connection), "head")
    command.check(config(migration_connection))
