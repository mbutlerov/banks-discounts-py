from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from sqlalchemy import text

from app.core.config import settings
from app.database.session import SessionLocal, engine
from app.database.models.bank import Bank
from app.database.models.promotion import Promotion
from app.promotions.availability import regenerate_occurrences
from app.promotions.schemas import OfferData
from app.database.models.offer import PromotionOffer
from app.database.models.ingestion import ScrapeRun
from app.scraping.runner import BANKS, RunBusy, load_replay, process_queue, recover_interrupted_runs, run_bank
from app.scraping.persistence import effective_offer


def refresh_calendar() -> int:
    from app.promotions.merchants import normalize_offer
    count = 0
    for bank in BANKS:
        lock_key = int.from_bytes(hashlib.sha256(("banks-discounts:" + bank).encode()).digest()[:8], "big", signed=True)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
            if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar():
                continue  # The active ingestion regenerates its own offers.
            try:
                with SessionLocal() as db:
                    offers = db.query(PromotionOffer).join(PromotionOffer.promotion).join(Promotion.bank).filter(Bank.slug == bank).all()
                    for offer in offers:
                        source = OfferData.model_validate(offer.data_jsonb).model_copy(update={"publication": offer.publication})
                        data = effective_offer(db, offer.promotion_id, source)
                        regenerate_occurrences(db, offer, data, horizon=90)
                        normalize_offer(db, offer.promotion, offer, data)
                    db.commit()
                    count += len(offers)
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
    return count


def schedule_due(bank: str) -> bool:
    with SessionLocal() as db:
        recent = db.query(ScrapeRun).filter(ScrapeRun.bank_slug == bank, ScrapeRun.finished_at.isnot(None)).order_by(ScrapeRun.finished_at.desc()).first()
        return recent is None or (datetime.now(timezone.utc) - recent.finished_at).total_seconds() >= settings.SCRAPING_INTERVAL_SECONDS


def main() -> int:
    parser = argparse.ArgumentParser(description="Corridas auditables y replay sin red.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--bank", choices=(*BANKS, "all"), required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--document-id", type=int, required=True)
    proposal = commands.add_parser("propose", help="Una propuesta IA con evidencia, sin modificar ofertas publicadas.")
    proposal.add_argument("--document-id", type=int, required=True)
    proposal.add_argument("--offer-id", type=int, required=True)
    ocr = commands.add_parser("ocr-review", help="OCR local de un snapshot PDF/imagen para revisión, sin publicar ofertas.")
    ocr.add_argument("--document-id", type=int, required=True)
    ocr.add_argument("--output-dir", default="data/review")
    ocr.add_argument("--language", default="spa")
    ocr.add_argument("--pages", type=int, nargs="+", help="Páginas específicas; por defecto hasta 40 páginas del documento.")
    commands.add_parser("refresh-calendar")
    normalize = commands.add_parser("normalize-merchants", help="Reconciliar comercios, reglas y locales existentes, sin red. Por defecto simula y revierte.")
    normalize.add_argument("--bank", choices=BANKS)
    normalize.add_argument("--apply", action="store_true", help="Confirmar los cambios; sin esta opción se hace rollback.")
    merchants = commands.add_parser("merchant-list", help="Consultar comercios, aliases, bancos y cantidad de locales.")
    merchants.add_argument("--search")
    merchants.add_argument("--limit", type=int, default=50)
    alias = commands.add_parser("merchant-alias", help="Asociar una referencia revisada a un comercio existente. Por defecto simula y revierte.")
    alias.add_argument("--namespace", required=True)
    alias.add_argument("--key", required=True)
    alias.add_argument("--merchant-slug", required=True)
    alias.add_argument("--reason", required=True)
    alias.add_argument("--source-url", required=True)
    alias.add_argument("--apply", action="store_true")
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--schedule", action="store_true", help="Además de la cola, actualizar los bancos cada intervalo configurado.")
    args = parser.parse_args()
    if args.command in {"normalize-merchants", "merchant-list", "merchant-alias"}:
        from app.promotions.merchant_management import assign_merchant_alias, lock_catalogue_banks, merchant_catalogue
        from app.promotions.merchants import backfill_merchants
        with SessionLocal() as db:
            try:
                if args.command == "merchant-list":
                    if not 1 <= args.limit <= 500:
                        parser.error("--limit debe estar entre 1 y 500.")
                    result = merchant_catalogue(db, search=args.search, limit=args.limit)
                else:
                    if args.command == "normalize-merchants":
                        lock_catalogue_banks(db, args.bank)
                        result = backfill_merchants(db, bank=args.bank)
                    else:
                        result = assign_merchant_alias(db, namespace=args.namespace, key=args.key, merchant_slug=args.merchant_slug, reason=args.reason, source_url=args.source_url)
                    errors = (result.get("backfill") or result).get("errors", [])
                    result["applied"] = args.apply and not bool(errors)
                    if result["applied"]:
                        db.commit()
                    else:
                        db.rollback()
                    if errors:
                        print(json.dumps(result, ensure_ascii=False))
                        return 1
            except ValueError as exc:
                db.rollback()
                parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "ocr-review":
        from app.database.models.ingestion import SourceDocument
        from app.scraping.ocr_review import OcrReviewError, review_document
        with SessionLocal() as db:
            document = db.get(SourceDocument, args.document_id)
            if document is None:
                parser.error("El documento no existe.")
            try:
                result = review_document(document, args.output_dir, language=args.language, pages=args.pages)
            except (OcrReviewError, ValueError, OSError) as exc:
                parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "propose":
        from app.scraping.ai_proposals import propose_offer
        with SessionLocal() as db:
            result = propose_offer(db, args.document_id, args.offer_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "refresh-calendar":
        print(json.dumps({"refreshed": refresh_calendar()}))
        return 0
    if args.command == "replay":
        bank, responses = load_replay(None, args.document_id)
        result = run_bank(bank, replay=responses)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["state"] == "failed" else 0
    if args.command == "run":
        results = [run_bank(bank) for bank in (BANKS if args.bank == "all" else (args.bank,))]
        print(json.dumps(results, ensure_ascii=False))
        return 1 if any(r["state"] == "failed" for r in results) else 0
    next_schedule = time.monotonic()
    next_calendar = time.monotonic()
    recovered = recover_interrupted_runs()
    print(json.dumps({"worker": "started", "schedule": args.schedule, "recovered": recovered}), flush=True)
    while True:
        process_queue()
        if args.schedule and time.monotonic() >= next_schedule:
            if time.monotonic() >= next_calendar:
                refresh_calendar()
                next_calendar = time.monotonic() + 86400
            for bank in settings.SCRAPING_BANKS.split(","):
                bank = bank.strip()
                if not bank:
                    continue
                if not schedule_due(bank):
                    continue
                try:
                    result = run_bank(bank.strip())
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                except RunBusy:
                    continue
            next_schedule = time.monotonic() + min(settings.SCRAPING_INTERVAL_SECONDS, 60)
        if args.once:
            return 0
        time.sleep(10)


if __name__ == "__main__":
    raise SystemExit(main())
