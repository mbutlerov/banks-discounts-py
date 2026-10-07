"""Slow adapters/replay must not leave PostgreSQL transactions idle."""

import hashlib
import json
import sys
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.database.models import ScrapeRun, SourceDocument
from app.scraping.clients.http_client import HttpClient, HttpResponseData
from app.scraping.schemas import ScrapedSource
from tests.helpers.ingestion import candidate, ingestion_engine


@pytest.fixture
def transaction_runner(ingestion_engine, monkeypatch, tmp_path):
    from app.scraping import runner

    application_name = "runner_transaction_test_" + uuid4().hex
    base = sa.create_engine(ingestion_engine.url, connect_args={
        "options": "-c idle_in_transaction_session_timeout=1000",
        "application_name": application_name,
    })
    engine = base.execution_options(**ingestion_engine.get_execution_options())
    monkeypatch.setattr(runner, "engine", engine)
    monkeypatch.setattr(runner, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(settings, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))

    def check_no_idle_transactions():
        with ingestion_engine.connect() as observer:
            assert observer.execute(sa.text("""
                SELECT count(*) FROM pg_stat_activity
                WHERE datname = current_database()
                  AND application_name = :name
                  AND state LIKE 'idle in transaction%'
            """), {"name": application_name}).scalar_one() == 0

    try:
        yield runner, engine, check_no_idle_transactions
    finally:
        base.dispose()


@pytest.mark.parametrize("crash", [False, True])
def test_slow_adapter_keeps_session_lock_without_idle_transactions(transaction_runner, ingestion_engine, monkeypatch, crash):
    from app.scraping import factories

    runner, engine, check_transactions = transaction_runner
    bank = "ueno"
    key = int.from_bytes(hashlib.sha256(b"banks-discounts:ueno").digest()[:8], "big", signed=True)
    url = "https://www.ueno.com.py/example"
    response = HttpResponseData(url, 200, "fixture", b"fixture", {"Content-Type": "text/html"})

    def check_exclusivity():
        check_transactions()
        with pytest.raises(runner.RunBusy):
            runner.run_bank(bank)

    class SlowSource:
        def fetch(self):
            check_exclusivity()
            HttpClient().get(url)
            time.sleep(1.3)  # Longer than the test connections' idle timeout.
            check_exclusivity()
            if crash:
                raise RuntimeError("Controlled adapter failure")
            return [ScrapedSource(source_type="html", source_url=url, text="fixture")]

    class SlowParser:
        def parse(self, source):
            check_exclusivity()
            time.sleep(1.3)
            check_exclusivity()
            return [candidate()]

    monkeypatch.setattr(factories, "get_source", lambda bank: SlowSource())
    monkeypatch.setattr(factories, "get_parser", lambda bank: SlowParser())
    result = runner.run_bank(bank, replay={url: response})
    assert result["state"] == ("failed" if crash else "success")
    assert result["counters"]["created"] == (0 if crash else 1)
    assert result["counters"]["fetched"] == 1
    with Session(engine) as db:
        assert db.query(SourceDocument).count() == 1
        assert db.query(ScrapeRun).one().state == result["state"]
    with ingestion_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
        assert observer.execute(sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one()
        assert observer.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key}).scalar_one()


def test_owned_replay_session_closes_before_slow_snapshot_reads(transaction_runner, monkeypatch):
    runner, engine, check_transactions = transaction_runner
    digest, path = runner.store_snapshot(b"replay fixture")
    url = "https://www.ueno.com.py/replay"
    with Session(engine) as db:
        run = ScrapeRun(bank_slug="ueno", state="failed")
        db.add(run)
        db.flush()
        document = SourceDocument(run_id=run.id, bank_slug="ueno", url=url,
                                  content_hash=digest, snapshot_path=path, http_status=200,
                                  mime_type="text/html")
        db.add(document)
        db.flush()
        identifier = document.id
        db.commit()
    original_read = runner.read_snapshot

    def slow_read(snapshot_path, expected_hash):
        check_transactions()
        time.sleep(1.3)
        check_transactions()
        return original_read(snapshot_path, expected_hash)

    monkeypatch.setattr(runner, "read_snapshot", slow_read)
    bank, responses = runner.load_replay(None, identifier)
    assert bank == "ueno"
    assert responses[url].content == b"replay fixture"


def test_cli_replay_uses_owned_metadata_session_and_runs_after_load(monkeypatch, capsys):
    from app.scraping import cli

    calls = []
    response = HttpResponseData("https://example.com", 200, "fixture", b"fixture", {})

    def load(db, identifier):
        assert db is None
        assert identifier == 7
        calls.append("loaded")
        return "ueno", {response.url: response}

    def run(bank, *, replay):
        assert calls == ["loaded"]
        assert bank == "ueno" and replay[response.url] is response
        calls.append("ran")
        return {"state": "success"}

    def unexpected_session():
        raise AssertionError("CLI retained a session around replay")

    monkeypatch.setattr(sys, "argv", ["scraping", "replay", "--document-id", "7"])
    monkeypatch.setattr(cli, "load_replay", load)
    monkeypatch.setattr(cli, "run_bank", run)
    monkeypatch.setattr(cli, "SessionLocal", unexpected_session)
    assert cli.main() == 0
    assert calls == ["loaded", "ran"]
    assert json.loads(capsys.readouterr().out) == {"state": "success"}
