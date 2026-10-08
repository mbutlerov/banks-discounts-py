from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.models.ingestion import PromotionOverride, ScrapeRun, SourceDocument
from app.database.models.offer import PromotionOffer
from app.database.session import get_db
from app.promotions.availability import regenerate_occurrences
from app.promotions.schemas import OfferData
from app.promotions.overrides import source_fingerprint
from app.promotions.merchants import normalize_offer
from app.scraping.persistence import apply_patch_data, effective_offer
from app.scraping.registry import ADAPTER_VERSION, BankSlug
from app.scraping.runner import serialize_run

router = APIRouter(prefix="/admin", tags=["administration"])
legacy_router = APIRouter(prefix="/scraping", tags=["retired"])
bearer = HTTPBearer(auto_error=False)


def require_admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    if not settings.ADMIN_API_KEY:
        raise HTTPException(503, "Administración deshabilitada: configurar ADMIN_API_KEY en privado.")
    if (
        len(request.headers.getlist("authorization")) != 1
        or not credentials
        or not secrets.compare_digest(credentials.credentials.encode("utf-8"), settings.ADMIN_API_KEY.encode("utf-8"))
    ):
        raise HTTPException(401, "Credencial administrativa inválida.", headers={"WWW-Authenticate": "Bearer"})


def lock_offer_bank(db: Session, offer: PromotionOffer) -> None:
    lock_key = int.from_bytes(hashlib.sha256(("banks-discounts:" + offer.promotion.bank.slug).encode()).digest()[:8], "big", signed=True)
    if not db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}).scalar():
        raise HTTPException(409, "El banco se está actualizando; reintentar la corrección al terminar.")
    db.refresh(offer)


class StartRun(BaseModel):
    bank_slug: BankSlug


class OverrideRequest(BaseModel):
    offer_key: str
    patch: dict
    reason: str = Field(min_length=3, max_length=1000)


@legacy_router.get("/{path:path}", deprecated=True)
def retired_get(path: str) -> None:
    raise HTTPException(410, "El scraping se ejecuta con CLI o POST /api/v1/admin/scrape-runs autenticado.")


@router.post("/scrape-runs", status_code=202, dependencies=[Depends(require_admin)])
def start_run(body: StartRun, db: Session = Depends(get_db)) -> dict:
    queue_key = int.from_bytes(hashlib.sha256(("banks-discounts:queue:" + body.bank_slug).encode()).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": queue_key})
    existing = db.query(ScrapeRun).filter(ScrapeRun.bank_slug == body.bank_slug, ScrapeRun.state.in_(["queued", "running"])).first()
    if existing:
        raise HTTPException(409, {"message": "El banco tiene una corrida pendiente.", "run_id": existing.id})
    run = ScrapeRun(bank_slug=body.bank_slug, state="queued", adapter_version=ADAPTER_VERSION)
    db.add(run)
    db.commit()
    return serialize_run(run)


@router.get("/scrape-runs", dependencies=[Depends(require_admin)])
def list_runs(db: Session = Depends(get_db)) -> list[dict]:
    return [serialize_run(r) for r in db.query(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(100)]


@router.get("/scrape-runs/{run_id}", dependencies=[Depends(require_admin)])
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(ScrapeRun, run_id)
    if not run:
        raise HTTPException(404, "Corrida inexistente.")
    return serialize_run(run)


@router.get("/source-documents", dependencies=[Depends(require_admin)])
def list_documents(run_id: str | None = None, bank_slug: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    query = db.query(SourceDocument)
    if run_id:
        query = query.filter(SourceDocument.run_id == run_id)
    if bank_slug:
        query = query.filter(SourceDocument.bank_slug == bank_slug)
    return [{"id": d.id, "run_id": d.run_id, "bank_slug": d.bank_slug, "url": d.url, "content_hash": d.content_hash, "mime_type": d.mime_type, "fetched_at": d.fetched_at.isoformat(), "parser_version": d.parser_version} for d in query.order_by(SourceDocument.id.desc()).limit(100)]


@router.get("/promotions/{promotion_id}/overrides", dependencies=[Depends(require_admin)])
def list_overrides(promotion_id: int, db: Session = Depends(get_db)) -> list[dict]:
    offers = {o.key: o for o in db.query(PromotionOffer).filter_by(promotion_id=promotion_id)}
    result = []
    for correction in db.query(PromotionOverride).filter_by(promotion_id=promotion_id).order_by(PromotionOverride.id):
        offer = offers.get(correction.offer_key)
        fingerprint = source_fingerprint(offer.data_jsonb) if offer else None
        result.append({"id": correction.id, "offer_key": correction.offer_key, "patch": correction.patch_jsonb, "reason": correction.reason, "active": correction.active, "source_changed": bool(correction.source_hash and correction.source_hash != fingerprint), "created_at": correction.created_at.isoformat()})
    return result


@router.post("/promotions/{promotion_id}/overrides", dependencies=[Depends(require_admin)])
def override_offer(promotion_id: int, body: OverrideRequest, db: Session = Depends(get_db)) -> dict:
    offer = db.query(PromotionOffer).filter_by(promotion_id=promotion_id, key=body.offer_key).first()
    if not offer:
        raise HTTPException(404, "Oferta inexistente.")
    lock_offer_bank(db, offer)
    if any(k in body.patch for k in ("key", "merchant_name", "merchant", "source_url", "schema_version")):
        raise HTTPException(422, "No se puede cambiar identidad mediante una corrección.")
    try:
        source = OfferData.model_validate(offer.data_jsonb).model_copy(update={"publication": offer.publication})
        effective = effective_offer(db, promotion_id, source)
        patched = apply_patch_data(effective, body.patch)
    except ValueError as exc:
        raise HTTPException(422, "Corrección incompatible con el schema de oferta.") from exc
    fingerprint = source_fingerprint(offer.data_jsonb)
    correction = PromotionOverride(promotion_id=promotion_id, offer_key=body.offer_key, patch_jsonb=body.patch, reason=body.reason, source_hash=fingerprint)
    db.add(correction)
    offer.version += 1
    regenerate_occurrences(db, offer, patched, horizon=90)
    db.flush()
    normalize_offer(db, offer.promotion, offer, patched)
    db.commit()
    return {"id": correction.id, "promotion_id": promotion_id, "offer_key": body.offer_key}


@router.delete("/overrides/{override_id}", dependencies=[Depends(require_admin)])
def deactivate_override(override_id: int, db: Session = Depends(get_db)) -> dict:
    correction = db.get(PromotionOverride, override_id)
    if correction is None:
        raise HTTPException(404, "Corrección inexistente.")
    sample_offer = db.query(PromotionOffer).filter_by(promotion_id=correction.promotion_id).first()
    if sample_offer:
        lock_offer_bank(db, sample_offer)
    correction.active = False
    db.flush()
    for offer in db.query(PromotionOffer).filter_by(promotion_id=correction.promotion_id):
        if correction.offer_key is None or offer.key == correction.offer_key:
            source = OfferData.model_validate(offer.data_jsonb).model_copy(update={"publication": offer.publication})
            data = effective_offer(db, offer.promotion_id, source)
            offer.version += 1
            regenerate_occurrences(db, offer, data, horizon=90)
            normalize_offer(db, offer.promotion, offer, data)
    db.commit()
    return {"id": correction.id, "active": False}
