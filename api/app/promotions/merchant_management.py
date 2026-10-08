"""Explicit catalogue maintenance; the caller owns commit or rollback."""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database.models.bank import Bank
from app.database.models.merchant_group import MerchantGroup
from app.database.models.merchant_location import MerchantLocation
from app.database.models.merchant_membership import MerchantAlias
from app.database.models.offer import PromotionOffer
from app.promotions.merchants import backfill_merchants


class CatalogueBusy(ValueError):
    pass


def lock_catalogue_banks(db: Session, bank: str | None = None) -> None:
    """Use the same locks as ingestion and manual corrections, in a fixed order."""
    query = db.query(Bank.slug)
    if bank:
        query = query.filter(Bank.slug == bank)
    slugs = [row.slug for row in query.order_by(Bank.slug)]
    if bank and not slugs:
        raise ValueError(f"Banco inexistente: {bank}.")
    for slug in slugs:
        key = int.from_bytes(hashlib.sha256(("banks-discounts:" + slug).encode()).digest()[:8], "big", signed=True)
        if not db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}).scalar():
            raise CatalogueBusy(f"El banco {slug} se está actualizando; reintentar al terminar.")


def merchant_catalogue(db: Session, *, search: str | None = None, limit: int = 50) -> dict:
    query = db.query(MerchantGroup)
    if search:
        pattern = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(MerchantGroup.name.ilike(f"%{pattern}%", escape="\\"))
    total = query.count()
    groups = query.order_by(MerchantGroup.name, MerchantGroup.id).limit(limit).all()
    identifiers = [group.id for group in groups]
    locations = dict(db.query(MerchantLocation.merchant_group_id, func.count()).filter(MerchantLocation.merchant_group_id.in_(identifiers)).group_by(MerchantLocation.merchant_group_id).all())
    aliases: dict[int, list[dict]] = {}
    for alias in db.query(MerchantAlias).filter(MerchantAlias.merchant_group_id.in_(identifiers)).order_by(MerchantAlias.namespace, MerchantAlias.source_key):
        aliases.setdefault(alias.merchant_group_id, []).append({"namespace": alias.namespace, "key": alias.source_key})
    banks: dict[int, list[str]] = {}
    for merchant_id, bank_slug in db.query(PromotionOffer.merchant_group_id, Bank.slug).join(PromotionOffer.promotion).join(Bank).filter(PromotionOffer.merchant_group_id.in_(identifiers)).distinct():
        banks.setdefault(merchant_id, []).append(bank_slug)
    return {
        "total": total,
        "unreconciled_offers": db.query(PromotionOffer).filter(PromotionOffer.merchant_group_id.is_(None)).count(),
        "items": [{"id": group.id, "slug": group.slug, "name": group.name, "locations": locations.get(group.id, 0), "banks": sorted(banks.get(group.id, [])), "aliases": aliases.get(group.id, [])} for group in groups],
    }


def assign_merchant_alias(db: Session, *, namespace: str, key: str, merchant_slug: str, reason: str, source_url: str) -> dict:
    """A reviewed source reference can identify a shared commerce across banks."""
    if not namespace.strip() or len(namespace) > 100 or not key.strip() or len(key) > 500:
        raise ValueError("Namespace y key deben respetar los límites de 100 y 500 caracteres.")
    if len(reason.strip()) < 3 or len(reason) > 1000:
        raise ValueError("Indicar un motivo de entre 3 y 1000 caracteres.")
    parts = urlsplit(source_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Indicar una URL de evidencia HTTP o HTTPS.")
    lock_catalogue_banks(db)
    group = db.query(MerchantGroup).filter_by(slug=merchant_slug).first()
    if group is None:
        raise ValueError("Comercio inexistente. Consultar merchant-list para obtener su slug.")
    alias = db.query(MerchantAlias).filter_by(namespace=namespace, source_key=key).first()
    previous = alias.merchant_group_id if alias else None
    if alias is None:
        alias = MerchantAlias(namespace=namespace, source_key=key, merchant_group_id=group.id)
        db.add(alias)
    # Keep a previously loaded relationship and the transaction-local identity
    # cache coherent when a reviewed alias moves to another merchant.
    alias.merchant_group = group
    alias.evidence_jsonb = {"method": "manual-review", "source_url": source_url, "reason": reason, "previous_merchant_id": previous}
    db.flush()
    report = backfill_merchants(db)
    return {"alias": {"namespace": namespace, "key": key, "merchant_slug": group.slug, "previous_merchant_id": previous, "merchant_id": group.id}, "backfill": report}
