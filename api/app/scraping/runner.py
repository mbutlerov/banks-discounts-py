from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.models.ingestion import ScrapeRun, SourceDocument
from app.database.models.promotion import Promotion
from app.promotions.availability import today_local
from app.database.session import SessionLocal, engine
from app.scraping.clients.http_client import HttpResponseData, http_scope
from app.scraping.persistence import reconcile_source_merchants, save_promotions
from app.scraping.registry import ADAPTER_VERSION, BANKS
from app.scraping.snapshots import read_snapshot, store_snapshot


class RunBusy(Exception):
    pass


class RunBudgetExceeded(Exception):
    pass


def serialize_run(run: ScrapeRun) -> dict:
    return {"id": run.id, "bank_slug": run.bank_slug, "state": run.state, "started_at": run.started_at.isoformat() if run.started_at else None, "finished_at": run.finished_at.isoformat() if run.finished_at else None, "counters": run.counters_jsonb, "errors": run.errors_jsonb, "adapter_version": run.adapter_version}


def _replay_metadata(db: Session, document_id: int) -> tuple[str, list[tuple]]:
    selected = db.get(SourceDocument, document_id)
    if selected is None:
        raise ValueError("Documento inexistente.")
    if selected.run_id:
        documents = db.query(
            SourceDocument.url, SourceDocument.http_status, SourceDocument.mime_type,
            SourceDocument.snapshot_path, SourceDocument.content_hash,
        ).filter_by(run_id=selected.run_id).order_by(SourceDocument.id).all()
    else:
        documents = [(selected.url, selected.http_status, selected.mime_type, selected.snapshot_path, selected.content_hash)]
    return selected.bank_slug, [tuple(document) for document in documents]


def load_replay(db: Session | None, document_id: int) -> tuple[str, dict[str, HttpResponseData]]:
    """With db=None, release the metadata session before reading snapshots.

    Existing callers supplying a session retain ownership of its transaction.
    """
    if db is None:
        with SessionLocal() as metadata_db:
            bank, documents = _replay_metadata(metadata_db, document_id)
    else:
        bank, documents = _replay_metadata(db, document_id)
    responses = {}
    for url, status, mime_type, path, content_hash in documents:
        data = read_snapshot(path, content_hash)
        responses[url] = HttpResponseData(url, status or 200, data.decode("utf-8", errors="replace"), data, {"Content-Type": mime_type or "application/octet-stream"})
    return bank, responses


def run_bank(bank: str, *, run_id: str | None = None, replay: dict[str, HttpResponseData] | None = None) -> dict:
    if bank not in BANKS:
        raise ValueError("Banco no soportado.")
    lock_key = int.from_bytes(hashlib.sha256(("banks-discounts:" + bank).encode()).digest()[:8], "big", signed=True)
    # Session locks survive commits. AUTOCOMMIT keeps this dedicated connection
    # out of an idle transaction while network/PDF work happens elsewhere.
    lock = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    acquired = False
    try:
        acquired = bool(lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar())
        if not acquired:
            raise RunBusy(f"Ya existe una corrida de {bank}.")
        with SessionLocal() as db:
            # Owning this bank lock proves earlier running rows have no active runner.
            for stale in db.query(ScrapeRun).filter_by(bank_slug=bank, state="running").all():
                stale.state = "failed"
                stale.finished_at = datetime.now(timezone.utc)
                stale.errors_jsonb = [{"stage": "worker", "message": "La ejecución anterior fue interrumpida."}]
            run = db.get(ScrapeRun, run_id) if run_id else None
            if run is not None and run.bank_slug != bank:
                raise ValueError("La corrida pertenece a otro banco.")
            if run is not None and run.state != "queued":
                return serialize_run(run)
            if run is None:
                run = ScrapeRun(bank_slug=bank, state="running", pid=os.getpid(), adapter_version=ADAPTER_VERSION)
                db.add(run)
            run.state = "running"
            # Queue entries may outlive a deployment: record the adapter actually executed.
            run.adapter_version = ADAPTER_VERSION
            run.pid = os.getpid()
            run.started_at = datetime.now(timezone.utc)
            run.finished_at = None
            db.flush()
            identifier = run.id
            previous = db.query(ScrapeRun).filter(ScrapeRun.bank_slug == bank, ScrapeRun.state == "success", ScrapeRun.id != identifier).order_by(ScrapeRun.finished_at.desc()).first()
            # Copy the values needed later rather than retaining an ORM session
            # (or lazily loading expired attributes) throughout the scrape.
            previous_id = previous.id if previous else None
            previous_started_at = previous.started_at if previous else None
            previous_count = (previous.counters_jsonb or {}).get("parsed", 0) if previous else 0
            db.commit()

        counters = {"discovered": 0, "fetched": 0, "parsed": 0, "offers": 0, "confirmed_offers": 0, "pending_offers": 0, "retired_offers": 0, "created": 0, "updated": 0, "rejected": 0, "retired": 0}
        errors: list[dict] = []
        started = time.monotonic()

        def observe(url: str, response: HttpResponseData | None, error: str | None) -> None:
            if time.monotonic() - started > settings.SCRAPING_TIMEOUT_SECONDS or counters["fetched"] >= settings.SCRAPING_MAX_DOCUMENTS:
                raise RunBudgetExceeded("Se alcanzó el presupuesto de la corrida.")
            if error:
                errors.append({"stage": "fetch", "url": url, "message": error[:400]})
                return
            if response:
                digest, path = store_snapshot(response.content)
                with SessionLocal() as documents_db:
                    documents_db.add(SourceDocument(run_id=identifier, bank_slug=bank, url=url, content_hash=digest, mime_type=response.headers.get("Content-Type", response.headers.get("content-type", ""))[:150], http_status=response.status_code, snapshot_path=path, parser_version=ADAPTER_VERSION))
                    documents_db.commit()
                counters["fetched"] += 1

        state = "failed"
        try:
            from app.scraping.factories import get_parser, get_source
            with http_scope(observe, replay):
                sources = get_source(bank).fetch()
                counters["discovered"] = len(sources)
                parser = get_parser(bank)
                promotions = []
                for source in sources:
                    if time.monotonic() - started > settings.SCRAPING_TIMEOUT_SECONDS:
                        raise RunBudgetExceeded("Se alcanzó el presupuesto de la corrida.")
                    try:
                        parsed = parser.parse(source)
                        for warning in ("discovery_warning", "parse_warning", "document_error"):
                            if (source.metadata or {}).get(warning):
                                errors.append({"stage": "discovery" if warning == "discovery_warning" else "parse", "url": source.source_url, "message": str(source.metadata[warning])[:500]})
                        if not parsed:
                            errors.append({"stage": "parse", "url": source.source_url, "message": "Fuente descubierta sin promociones interpretables."})
                        promotions.extend(parsed)
                    except Exception as exc:
                        errors.append({"stage": "parse", "url": source.source_url, "message": f"{type(exc).__name__}: {str(exc)[:300]}"})
                        counters["rejected"] += 1
                counters["parsed"] = len(promotions)

            with SessionLocal() as db:
                run = db.get(ScrapeRun, identifier)
                documents_by_url = {d.url: d for d in db.query(SourceDocument).filter_by(run_id=identifier).order_by(SourceDocument.id)}
                for promotion in promotions:
                    for offer in getattr(promotion, "offers", []):
                        for evidence in [*offer.evidence, *offer.schedule.evidence, *(item for location in offer.locations for item in location.evidence)]:
                            document = documents_by_url.get(evidence.source_url or offer.source_url)
                            if document:
                                evidence.document_id = document.id
                counters["offers"] = sum(len(getattr(p, "offers", [])) for p in promotions)
                counters["confirmed_offers"] = sum(o.publication == "confirmed" for p in promotions for o in getattr(p, "offers", []))
                counters["pending_offers"] = sum(o.publication == "pending" for p in promotions for o in getattr(p, "offers", []))
                counters["retired_offers"] = sum(o.publication == "retired" for p in promotions for o in getattr(p, "offers", []))
                if previous_count >= 20 and counters["parsed"] < previous_count / 2:
                    errors.append({"stage": "coverage", "message": "La cobertura cayó por debajo de la mitad de la última corrida completa. Revisar discovery; no se retiran ausencias."})
                if not promotions:
                    errors.append({"stage": "discovery", "message": "Catálogo vacío o no disponible; se conservan los datos anteriores."})
                db.flush()
                # Persist each candidate atomically, so one invalid record cannot destroy a run.
                accepted = []
                for promotion in promotions:
                    try:
                        with db.begin_nested():
                            created, updated = save_promotions(db, [promotion], run_id=identifier, commit=False)
                        counters["created"] += created
                        counters["updated"] += updated
                        accepted.append(promotion)
                    except Exception as exc:
                        errors.append({"stage": "persist", "message": f"{type(exc).__name__}: candidato rechazado."})
                        counters["rejected"] += 1
                counters["retired"] += reconcile_source_merchants(db, promotions, accepted)
                state = "failed" if not promotions or not (counters["created"] + counters["updated"]) else "partial" if errors else "success"
                run.state = state
                if state == "success" and replay is None and settings.SCRAPING_RETIRE_MISSING and previous_id:
                    # Absence alone is never enough: require two complete runs AND expired validity.
                    for absent in db.query(Promotion).filter(Promotion.source_key.isnot(None), Promotion.last_run_id.notin_([identifier, previous_id]), Promotion.last_seen_at < previous_started_at, Promotion.publication != "retired").all():
                        if absent.bank.slug != bank or not absent.offers:
                            continue
                        from app.promotions.schemas import OfferData
                        from app.scraping.persistence import effective_offer
                        variants = [effective_offer(db, absent.id, OfferData.model_validate(o.data_jsonb)) for o in absent.offers]
                        if all(v.validity_state == "known" and v.valid_until and v.valid_until < today_local() for v in variants):
                            absent.publication = "retired"
                            counters["retired"] += 1
                db.commit()
        except Exception as exc:
            # The short-lived persistence session rolls back before we reopen
            # one to audit the failure. Downloaded source documents survive.
            counters["created"] = counters["updated"] = 0
            errors.append({"stage": "run", "message": f"{type(exc).__name__}: {str(exc)[:300]}"})
            state = "failed"

        with SessionLocal() as db:
            run = db.get(ScrapeRun, identifier)
            run.state = state
            run.counters_jsonb = counters
            counters["elapsed_seconds"] = round(time.monotonic() - started, 2)
            run.errors_jsonb = errors
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
            return serialize_run(run)
    finally:
        try:
            if acquired:
                lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
        finally:
            lock.close()


def process_queue() -> int:
    with SessionLocal() as db:
        queued = [(r.id, r.bank_slug) for r in db.query(ScrapeRun).filter_by(state="queued").order_by(ScrapeRun.started_at).all()]
    processed = 0
    for identifier, bank in queued:
        try:
            run_bank(bank, run_id=identifier)
            processed += 1
        except RunBusy:
            continue
    return processed


def recover_interrupted_runs() -> int:
    """A process restart must not leave a bank permanently stuck in running."""
    recovered = 0
    for bank in BANKS:
        lock_key = int.from_bytes(hashlib.sha256(("banks-discounts:" + bank).encode()).digest()[:8], "big", signed=True)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
            if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar():
                continue
            try:
                with SessionLocal() as db:
                    queue_key = int.from_bytes(hashlib.sha256(("banks-discounts:queue:" + bank).encode()).digest()[:8], "big", signed=True)
                    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": queue_key})
                    interrupted = db.query(ScrapeRun).filter_by(bank_slug=bank, state="running").all()
                    for run in interrupted:
                        run.state = "failed"
                        run.finished_at = datetime.now(timezone.utc)
                        run.errors_jsonb = [{"stage": "worker", "message": "Ejecución interrumpida; nueva corrida en cola."}]
                    if interrupted and not db.query(ScrapeRun).filter_by(bank_slug=bank, state="queued").first():
                        db.add(ScrapeRun(bank_slug=bank, state="queued", adapter_version=ADAPTER_VERSION))
                    recovered += len(interrupted)
                    db.commit()
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
    return recovered
