from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.database.models.bank import Bank
from app.database.models.category import Category
from app.database.models.ingestion import PromotionOverride
from app.database.models.offer import PromotionOffer
from app.database.models.promotion import Promotion
from app.promotions.availability import regenerate_occurrences, today_local
from app.promotions.schemas import OfferData
from app.promotions.overrides import effective_offer as overlay_offer
from app.promotions.merchants import normalize_offer
from app.scraping.schemas import ScrapedPromotion
from app.scraping.utils.categorizer import categorize


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def source_key(scraped: ScrapedPromotion) -> str:
    explicit = getattr(scraped, "source_key", None)
    if explicit:
        return _bounded_key(explicit)
    meta = scraped.metadata or {}
    if meta.get("b") and meta.get("c"):
        return f"itau:{meta['b']}:{meta['c']}"
    if meta.get("source_url"):
        return _bounded_key(canonical_url(meta["source_url"]))
    raise ValueError("Una promoción necesita identidad de fuente, no solo título.")


def _bounded_key(value: str) -> str:
    return value if len(value) <= 500 else value[:430] + ":" + hashlib.sha256(value.encode()).hexdigest()


def _observed(value: object) -> bool:
    """Empty extraction is absence of evidence; explicit corrections use overrides."""
    return value is not None and value != "" and value != [] and value != {}


def _slug(bank: str, title: str, key: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    clean = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:65]
    return f"{bank}-{clean}-{hashlib.sha256(key.encode()).hexdigest()[:10]}"


def apply_patch_data(data: OfferData, patch: dict) -> OfferData:
    def merge(base: dict, update: dict) -> dict:
        result = dict(base)
        for name, value in update.items():
            result[name] = merge(result[name], value) if isinstance(value, dict) and isinstance(result.get(name), dict) else value
        return result
    return OfferData.model_validate(merge(data.model_dump(mode="json"), patch))


def effective_offer(db: Session, promotion_id: int, data: OfferData) -> OfferData:
    overrides = db.query(PromotionOverride).filter_by(promotion_id=promotion_id, active=True).order_by(PromotionOverride.id).all()
    return overlay_offer(data, overrides)


def _legacy_match(db: Session, bank_id: int, scraped: ScrapedPromotion) -> Promotion | None:
    if ":merchant:" in (getattr(scraped, "source_key", None) or ""):
        return None
    url = (scraped.metadata or {}).get("source_url")
    if not url:
        return None
    candidates = db.query(Promotion).filter(Promotion.bank_id == bank_id, Promotion.source_key.is_(None)).all()
    matches = [p for p in candidates if canonical_url((p.metadata_jsonb or {}).get("source_url", "")) == canonical_url(url)]
    # Ambiguity is left for review; never silently merge several old records.
    return matches[0] if len(matches) == 1 else None


def save_promotions(db: Session, promotions: list[ScrapedPromotion], *, upsert: bool = True, run_id: str | None = None, commit: bool = True) -> tuple[int, int]:
    created = updated = 0
    seen: set[tuple[int, str]] = set()
    now = datetime.now(timezone.utc)
    banks = {b.slug: b for b in db.query(Bank).all()}
    categories = {c.slug: c for c in db.query(Category).all()}
    for scraped in promotions:
        bank = banks.get(scraped.bank_slug)
        if bank is None:
            raise ValueError(f"Banco no inicializado: {scraped.bank_slug}. Ejecutar seed.")
        key = source_key(scraped)
        if (bank.id, key) in seen:
            continue
        seen.add((bank.id, key))
        record = db.query(Promotion).filter_by(bank_id=bank.id, source_key=key).first() or _legacy_match(db, bank.id, scraped)
        if record and not upsert:
            continue
        category_slug = categorize(scraped.title, scraped.description, scraped.category_name)
        category = categories.get(category_slug)
        if record is None:
            record = Promotion(bank_id=bank.id, source_key=key, slug=_slug(bank.slug, scraped.title, key), title=scraped.title[:180], status="draft", publication="pending", applies_to_all_locations=False)
            db.add(record)
            created += 1
        else:
            updated += 1
        record.source_key = key
        record.title = scraped.title[:180]
        record.last_seen_at = now
        record.last_run_id = run_id
        # Failed/absent field extraction must not erase earlier observations.
        for name, value in {"description": scraped.description, "terms_summary": scraped.terms_summary, "benefit_type": scraped.benefit_type, "mechanic_type": scraped.mechanic_type, "discount_percentage": scraped.discount_percentage, "start_date": scraped.start_date, "end_date": scraped.end_date}.items():
            if _observed(value):
                setattr(record, name, value)
        if scraped.description:
            record.short_description = scraped.description[:255]
        if category and (category.slug != "other" or record.category_id is None):
            record.category_id = category.id
        # These warnings describe the current observation, not permanent
        # merchant attributes. A successful reparse must clear old warnings;
        # keep historical diagnostics in ScrapeRun.errors_jsonb.
        previous_metadata = dict(record.metadata_jsonb or {})
        for warning in ("parse_warning", "document_error", "discovery_warning"):
            previous_metadata.pop(warning, None)
        record.metadata_jsonb = {**previous_metadata, **{k: v for k, v in (scraped.metadata or {}).items() if _observed(v)}}
        today = today_local()
        record.status = "expired" if record.end_date and record.end_date < today else "draft" if record.start_date and record.start_date > today else "active"
        db.flush()
        confirmed = False
        corrections = db.query(PromotionOverride).filter_by(promotion_id=record.id, active=True).order_by(PromotionOverride.id).all()
        observed_keys: set[str] = set()
        for candidate in getattr(scraped, "offers", []):
            data = OfferData.model_validate(candidate)
            observed_keys.add(data.key)
            stored = db.query(PromotionOffer).filter_by(promotion_id=record.id, key=data.key).first()
            if stored is None:
                stored = PromotionOffer(promotion_id=record.id, key=data.key, source_key=key, data_jsonb=data.model_dump(mode="json"), publication=data.publication, version=1)
                db.add(stored)
            elif data.publication == "pending" and stored.publication == "confirmed":
                # Preserve original fields but withdraw confirmation of a degraded observation.
                old = OfferData.model_validate(stored.data_jsonb)
                stored.publication = "pending"
                old = old.model_copy(update={"publication": "pending"})
                stored.data_jsonb = old.model_dump(mode="json")
                stored.version += 1
                data = old
            else:
                serialized = data.model_dump(mode="json")
                if stored.data_jsonb != serialized or stored.publication != data.publication:
                    stored.data_jsonb = serialized
                    stored.publication = data.publication
                    stored.version += 1
            db.flush()
            effective = overlay_offer(data, corrections)
            normalize_offer(db, record, stored, effective, corrections=corrections)
            regenerate_occurrences(db, stored, effective, today, horizon=90)
            confirmed = confirmed or effective.publication == "confirmed"
        if observed_keys:
            complete = (scraped.metadata or {}).get("offers_complete") == "true" and not any((scraped.metadata or {}).get(warning) for warning in ("parse_warning", "document_error"))
            for missing in db.query(PromotionOffer).filter(PromotionOffer.promotion_id == record.id, PromotionOffer.key.notin_(observed_keys), PromotionOffer.publication != "retired"):
                publication = "retired" if complete else "pending"
                if missing.publication != publication:
                    preserved = OfferData.model_validate(missing.data_jsonb).model_copy(update={"publication": publication})
                    missing.data_jsonb = preserved.model_dump(mode="json")
                    missing.publication = publication
                    missing.version += 1
                    effective = overlay_offer(preserved, corrections)
                    normalize_offer(db, record, missing, effective, corrections=corrections)
                    regenerate_occurrences(db, missing, effective, today, horizon=90)
                    confirmed = confirmed or effective.publication == "confirmed"
        if confirmed:
            record.publication = "confirmed"
            record.last_verified_at = now
        else:
            record.publication = "pending"
    if commit:
        db.commit()
    return created, updated


def reconcile_source_merchants(db: Session, parsed: list[ScrapedPromotion], accepted: list[ScrapedPromotion]) -> int:
    """Replace obsolete merchant groups only within a document actually re-parsed."""
    groups: dict[tuple[str, str], list[ScrapedPromotion]] = {}
    accepted_keys = {(p.bank_slug, source_key(p)) for p in accepted}
    for promotion in parsed:
        try:
            key = source_key(promotion)
        except ValueError:
            continue
        if ":merchant:" in key:
            groups.setdefault((promotion.bank_slug, key.rsplit(":merchant:", 1)[0]), []).append(promotion)
    banks = {b.slug: b.id for b in db.query(Bank)}
    retired = 0
    for (bank, base), candidates in groups.items():
        keys = {source_key(p) for p in candidates}
        if not all((bank, key) in accepted_keys for key in keys):
            continue
        complete = all(p.metadata.get("offers_complete") == "true" and not any(p.metadata.get(w) for w in ("parse_warning", "document_error")) for p in candidates)
        publication = "retired" if complete else "pending"
        previous = db.query(Promotion).filter(Promotion.bank_id == banks[bank], Promotion.source_key.startswith(base + ":merchant:", autoescape=True), Promotion.source_key.notin_(keys), Promotion.publication != "retired").all()
        for parent in previous:
            parent.publication = publication
            corrections = db.query(PromotionOverride).filter_by(promotion_id=parent.id, active=True).order_by(PromotionOverride.id).all()
            if complete:
                retired += 1
            for offer in parent.offers:
                if offer.publication != "retired" and offer.publication != publication:
                    data = OfferData.model_validate(offer.data_jsonb).model_copy(update={"publication": publication})
                    offer.publication = publication
                    offer.data_jsonb = data.model_dump(mode="json")
                    offer.version += 1
                    effective = overlay_offer(data, corrections)
                    normalize_offer(db, parent, offer, effective, corrections=corrections)
                    regenerate_occurrences(db, offer, effective, today_local(), horizon=90)
    return retired
