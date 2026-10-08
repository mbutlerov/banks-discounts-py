from __future__ import annotations

from datetime import date as Date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import exists, func, or_
from sqlalchemy.orm import Session, selectinload

from app.database.models.bank import Bank
from app.database.models.category import Category
from app.database.models.promotion import Promotion
from app.database.models.ingestion import PromotionOverride
from app.database.models.offer import OfferOccurrence, PromotionOffer
from app.database.models.merchant_membership import MerchantOfferRule, OfferLocation
from app.database.session import get_db
from app.promotions.availability import MAX_QUERY_DAYS, applies_on, availability_state, matching_dates, today_local
from app.promotions.legacy import legacy_offer
from app.promotions.locations import chain_group, collapse_chain_variants, prepare_chain_offer, presentation_locations, search_matches
from app.promotions.merchants import normalized_grouping, normalized_offer
from app.promotions.overrides import effective_offer
from app.promotions.schemas import CatalogItem, OfferData, OfferResponse, PromotionGrouping, PromotionResponse, PromotionsResponse

router = APIRouter(prefix="/promotions", tags=["promotions"])
DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DayName = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def resolve_range(selected: Date | None, first: Date | None, last: Date | None, day: str | None = None) -> tuple[Date, Date]:
    if selected and (first or last):
        raise HTTPException(422, "Use date or date_from/date_to, not both")
    if selected:
        return selected, selected
    if first is None and last is None:
        first = today_local()
        if day:
            first += timedelta(days=(DAY_NAMES.index(day) + 1 - first.isoweekday()) % 7)
        return first, first
    first = first or today_local()
    last = last or first
    if last < first:
        raise HTTPException(422, "date_to must not be before date_from")
    if (last - first).days >= MAX_QUERY_DAYS:
        raise HTTPException(422, f"Date range cannot exceed {MAX_QUERY_DAYS} days")
    return first, last


def benefit_matches(offer: OfferData, kind: str | None, minimum: Decimal | None, maximum: Decimal | None) -> bool:
    """Type and percentage must belong to the same benefit in the same variant."""
    if kind is None and minimum is None and maximum is None:
        return True
    return any(
        (kind is None or benefit.type == kind)
        and (minimum is None or benefit.percentage is not None and benefit.percentage >= minimum)
        and (maximum is None or benefit.percentage is not None and benefit.percentage <= maximum)
        for benefit in offer.benefits
    )


def _exact_requirement(value: str | None, choices: list[str]) -> bool:
    return value is None or value.casefold() in {choice.casefold() for choice in choices}


def _offers(promotion: Promotion) -> list[OfferData]:
    if not promotion.offers:
        return [legacy_offer(promotion)]
    result = []
    for row in promotion.offers:
        if row.publication not in {"confirmed", "pending", "retired"}:
            continue
        try:
            data = OfferData.model_validate(row.data_jsonb)
        except ValidationError:
            continue
        if row.publication != data.publication:
            data = data.model_copy(update={"publication": row.publication})
        data = normalized_offer(row, data)
        result.append(data)
    return result


def _load_options() -> tuple:
    """Load the merchant catalogue in batches, including physical memberships."""
    return (
        selectinload(Promotion.bank), selectinload(Promotion.category),
        selectinload(Promotion.offers).selectinload(PromotionOffer.merchant_group),
        selectinload(Promotion.offers).selectinload(PromotionOffer.rule),
        selectinload(Promotion.offers).selectinload(PromotionOffer.location_memberships).selectinload(OfferLocation.location),
    )


def _effective_data(p: Promotion, data: OfferData, corrections: list[PromotionOverride]) -> OfferData:
    # Derive payment routes from the original annex before manual corrections.
    # Otherwise an explicit channel correction could be overwritten by its PDF.
    if data.merchant is None and chain_group(p.bank.slug, data):
        data = prepare_chain_offer(data)
    data = effective_offer(data, corrections)
    patches = [row.patch_jsonb for row in corrections if row.active and row.offer_key in (None, data.key)]
    eligibility_patches = [patch.get("eligibility", {}) for patch in patches]
    if not any("locations" in patch for patch in patches) and any(
        "locations" in patch or "cities" in patch for patch in eligibility_patches
    ):
        # A correction of an address invalidates the old annex association until
        # the reviewer supplies the corresponding structured location.
        data = data.model_copy(update={"locations": []})
    elif data.locations:
        changes = {}
        for field in ("channels", "processors"):
            if any(field in patch for patch in eligibility_patches):
                changes[field] = getattr(data.eligibility, field)
        if changes:
            data = data.model_copy(update={"locations": [location.model_copy(update=changes) for location in data.locations]})
    return data


def _grouping(p: Promotion, row: PromotionOffer | None, data: OfferData, corrections: list[PromotionOverride]) -> PromotionGrouping | None:
    # Merchant corrections remain visible immediately, even before the catalogue
    # has been reconciled. Never display a stale relational merchant instead.
    identity_corrected = any(
        correction.active and correction.offer_key in (None, data.key)
        and any(field in correction.patch_jsonb for field in ("merchant", "merchant_name"))
        for correction in corrections
    )
    if identity_corrected:
        return None
    if row is not None and not identity_corrected:
        group = normalized_grouping(p, row)
        if group:
            return group
    return chain_group(p.bank.slug, data)


def _variant(data: OfferData, first: Date, last: Date, weekday: int | None = None, indexed_dates: list[Date] | None = None) -> OfferResponse:
    dates = matching_dates(data, first, last, weekday) if indexed_dates is None else [selected for selected in indexed_dates if (weekday is None or selected.isoweekday() == weekday) and applies_on(data, selected)]
    state = availability_state(data)
    if state == "confirmed" and not dates:
        state = "not_applicable"
    return OfferResponse(**data.model_dump(), matched_dates=dates, availability=state)


def _serialize(p: Promotion, merchant: str | None, variants: list[OfferResponse], grouping: PromotionGrouping | None = None) -> PromotionResponse:
    meta = p.metadata_jsonb if isinstance(p.metadata_jsonb, dict) else {}
    if grouping:
        variants, locations = collapse_chain_variants(variants, grouping)
        merchant = grouping.name
    else:
        locations = presentation_locations(variants)
    dates = sorted({selected for variant in variants for selected in variant.matched_dates})
    percentages = [benefit.percentage for variant in variants for benefit in variant.benefits if benefit.percentage is not None]
    installments = [benefit.installments for variant in variants for benefit in variant.benefits if benefit.installments]
    kinds = list(dict.fromkeys(benefit.type for variant in variants for benefit in variant.benefits))
    cards = list(dict.fromkeys(card for variant in variants for card in variant.eligibility.cards))
    weekdays = sorted({weekday for variant in variants if variant.schedule.state == "known" and variant.schedule.kind in {"weekly", "all_days"} for weekday in (range(1, 8) if variant.schedule.kind == "all_days" else variant.schedule.weekdays) if weekday not in variant.schedule.excluded_weekdays})
    starts = [variant.valid_from for variant in variants]
    ends = [variant.valid_until for variant in variants]
    state = "confirmed" if dates else "conflict" if any(v.availability == "conflict" for v in variants) else "unknown" if any(v.availability == "unknown" for v in variants) else "not_applicable"
    return PromotionResponse(
        slug=p.slug, title=merchant or p.title, merchant_name=merchant,
        short_description=p.short_description, description=p.description,
        benefit_type=kinds[0] if len(kinds) == 1 else "other" if kinds else p.benefit_type,
        discount_percentage=max(percentages) if percentages else None,
        start_date=min(starts) if starts and all(starts) else None,
        end_date=max(ends) if ends and all(ends) else None,
        status="active" if dates else "pending" if state in {"unknown", "conflict"} else "unavailable",
        pdf_url=meta.get("pdf_url") if isinstance(meta.get("pdf_url"), str) else None,
        source_url=meta.get("source_url") if isinstance(meta.get("source_url"), str) else None,
        valid_days=[DAY_NAMES[day - 1] for day in weekdays] or None,
        installments=max(installments) if installments else None,
        applicable_cards=cards or None,
        category=CatalogItem(slug=p.category.slug, name=p.category.name) if p.category else None,
        bank=CatalogItem(slug=p.bank.slug, name=p.bank.name),
        matched_dates=dates, variants=variants, availability=state,
        last_checked_at=p.last_verified_at, terms_summary=p.terms_summary,
        grouping=grouping, locations=locations,
        location_scope="all" if variants and all(variant.location_scope == "all" for variant in variants)
        else "specified" if locations or any(variant.location_scope == "specified" for variant in variants)
        else "unknown",
    )


@router.get("", response_model=PromotionsResponse)
def list_promotions(
    db: Session = Depends(get_db),
    date: Date | None = Query(default=None),
    date_from: Date | None = Query(default=None),
    date_to: Date | None = Query(default=None),
    bank_slug: list[str] | None = Query(default=None),
    benefit_type: Literal["discount", "cashback", "installments", "points", "other"] | None = Query(default=None),
    status: Literal["active", "pending", "all"] = Query(default="active"),
    min_discount: Decimal | None = Query(default=None, ge=0, le=100),
    max_discount: Decimal | None = Query(default=None, ge=0, le=100),
    search: str | None = Query(default=None, max_length=100),
    day: DayName | None = Query(default=None),
    category_slug: str | None = Query(default=None),
    card: str | None = Query(default=None, max_length=100),
    level: str | None = Query(default=None, max_length=100),
    channel: str | None = Query(default=None, max_length=100),
    include_pending: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> PromotionsResponse:
    first, last = resolve_range(date, date_from, date_to, day)
    if min_discount is not None and max_discount is not None and min_discount > max_discount:
        raise HTTPException(422, "min_discount must not exceed max_discount")
    include_pending = include_pending or status in {"pending", "all"}
    q = db.query(Promotion).join(Promotion.bank).outerjoin(Promotion.category).options(
        *_load_options()
    ).filter(Promotion.publication != "retired", Bank.is_active.is_(True))
    if bank_slug:
        slugs = [slug for value in bank_slug for slug in value.split(",") if slug]
        q = q.filter(Bank.slug.in_(slugs))
    if category_slug:
        q = q.filter(Category.slug == category_slug)
    # All variant filters precede grouping, counting and paging. The calendar
    # evaluator remains authoritative outside/stale occurrence coverage too.
    results: list[PromotionResponse] = []
    weekday = DAY_NAMES.index(day) + 1 if day else None
    needle = search.casefold().strip() if search else None
    if not include_pending:
        # A complete index can eliminate nonmatching campaigns in PostgreSQL.
        # Unknown coverage/version and manual corrections always reach the pure
        # evaluator. This prefilter never paginates campaigns before their offers.
        fallback = exists().where(
            PromotionOffer.promotion_id == Promotion.id,
            or_(PromotionOffer.coverage_from.is_(None), PromotionOffer.coverage_until.is_(None),
                PromotionOffer.coverage_from > first, PromotionOffer.coverage_until < last,
                PromotionOffer.occurrences_version.is_(None), PromotionOffer.occurrences_version != PromotionOffer.version),
        )
        corrections = exists().where(PromotionOverride.promotion_id == Promotion.id, PromotionOverride.active.is_(True))
        indexed_match = exists().where(
            PromotionOffer.promotion_id == Promotion.id,
            PromotionOffer.coverage_from <= first, PromotionOffer.coverage_until >= last,
            PromotionOffer.occurrences_version == PromotionOffer.version,
            OfferOccurrence.offer_id == PromotionOffer.id,
            OfferOccurrence.rules_version == PromotionOffer.version,
            OfferOccurrence.applies_on >= first, OfferOccurrence.applies_on <= last,
        )
        if weekday:
            indexed_match = indexed_match.where(func.extract("isodow", OfferOccurrence.applies_on) == weekday)
        q = q.filter(or_(fallback, corrections, indexed_match))
    candidates = q.order_by(Promotion.id).all()
    overrides: dict[int, list[PromotionOverride]] = {}
    if candidates:
        for row in db.query(PromotionOverride).filter(PromotionOverride.active.is_(True), PromotionOverride.promotion_id.in_([p.id for p in candidates])).order_by(PromotionOverride.id):
            overrides.setdefault(row.promotion_id, []).append(row)
    covered = {
        row.id: row for p in candidates if not overrides.get(p.id) for row in p.offers
        if row.coverage_from is not None and row.coverage_until is not None
        and row.coverage_from <= first and row.coverage_until >= last
        and row.occurrences_version == row.version
    }
    indexed: dict[int, list[Date]] = {}
    if covered:
        for occurrence in db.query(OfferOccurrence).filter(OfferOccurrence.offer_id.in_(covered), OfferOccurrence.applies_on >= first, OfferOccurrence.applies_on <= last).order_by(OfferOccurrence.applies_on):
            # A concurrent regeneration may have committed between reads. Never
            # use occurrences for a different rule version; evaluator is fallback.
            if occurrence.rules_version == covered[occurrence.offer_id].version:
                indexed.setdefault(occurrence.offer_id, []).append(occurrence.applies_on)
    groups: dict[tuple, tuple[Promotion, str, list[OfferResponse], PromotionGrouping | None]] = {}
    for p in candidates:
        rows_by_key = {row.key: row for row in p.offers}
        for data in _offers(p):
            row = rows_by_key.get(data.key)
            try:
                data = _effective_data(p, data, overrides.get(p.id, []))
            except ValidationError:
                continue
            grouping = _grouping(p, row, data, overrides.get(p.id, []))
            if data.publication == "retired" or not benefit_matches(data, benefit_type, min_discount, max_discount):
                continue
            if not _exact_requirement(card, data.eligibility.cards) or not _exact_requirement(level, data.eligibility.levels) or not _exact_requirement(channel, data.eligibility.channels):
                continue
            search_title = f"{p.title} {grouping.name}" if grouping else p.title
            if needle and not search_matches(data, search_title, needle):
                continue
            cached = indexed.get(row.id) if row is not None else None
            variant = _variant(data, first, last, weekday, cached)
            if not variant.matched_dates and not (include_pending and variant.availability in {"unknown", "conflict"}):
                continue
            if status == "pending" and variant.availability not in {"unknown", "conflict"}:
                continue
            # Stable merchant and bank/campaign context group branch observations
            # before counting and paging. Unnormalized rows retain the PDF proof.
            key = ("chain", grouping.key) if grouping else ("merchant", p.id, data.merchant_name.strip().casefold())
            if key not in groups:
                groups[key] = (p, grouping.name if grouping else data.merchant_name, [], grouping)
            if grouping:
                variant = variant.model_copy(update={"key": f"{p.id}:{variant.key}"})
            groups[key][2].append(variant)
    results.extend(_serialize(parent, merchant, variants, grouping) for parent, merchant, variants, grouping in groups.values())
    results.sort(key=lambda item: (item.bank.name.casefold(), (item.merchant_name or item.title).casefold(), item.slug))
    offset = (page - 1) * size
    return PromotionsResponse(total=len(results), page=page, size=size, date_from=first, date_to=last, items=results[offset:offset + size])


@router.get("/{slug}", response_model=PromotionResponse)
def promotion_detail(
    slug: str,
    db: Session = Depends(get_db),
    date: Date | None = Query(default=None),
    date_from: Date | None = Query(default=None),
    date_to: Date | None = Query(default=None),
) -> PromotionResponse:
    first, last = resolve_range(date, date_from, date_to)
    p = db.query(Promotion).options(*_load_options()).filter(Promotion.slug == slug).first()
    if p is None:
        raise HTTPException(404, "Promotion not found")
    initial_corrections = db.query(PromotionOverride).filter(PromotionOverride.active.is_(True), PromotionOverride.promotion_id == p.id).order_by(PromotionOverride.id).all()
    target_groups: dict[str, tuple[PromotionGrouping, PromotionOffer | None]] = {}
    ungrouped = False
    initial_rows = {row.key: row for row in p.offers}
    for original in _offers(p):
        try:
            data = _effective_data(p, original, initial_corrections)
        except ValidationError:
            continue
        if data.publication == "retired":
            continue
        row = initial_rows.get(original.key)
        group = _grouping(p, row, data, initial_corrections)
        if group:
            target_groups[group.key] = (group, row)
        else:
            ungrouped = True
    # A campaign URL can contain multiple merchants. In that case the original
    # detail keeps every offer instead of arbitrarily selecting its first chain.
    target = next(iter(target_groups.values())) if len(target_groups) == 1 and not ungrouped else None
    grouping, target_row = target if target else (None, None)
    parents = [p]
    if grouping:
        # Old branch URLs resolve the same merchant and campaign. The normalized
        # lookup uses indexed identities instead of scanning the entire bank.
        q = db.query(Promotion).options(*_load_options()).filter(
            Promotion.bank_id == p.bank_id, Promotion.publication != "retired")
        if target_row is not None and target_row.rule is not None and normalized_grouping(p, target_row):
            q = q.join(Promotion.offers).join(PromotionOffer.rule).filter(
                PromotionOffer.merchant_group_id == target_row.merchant_group_id,
                MerchantOfferRule.context_key == target_row.rule.context_key,
                MerchantOfferRule.bank_id == p.bank_id,
            ).distinct()
        else:
            # Compatibility for observations that predate merchant normalization.
            source_url = (p.metadata_jsonb or {}).get("source_url") if isinstance(p.metadata_jsonb, dict) else None
            if isinstance(source_url, str):
                q = q.filter(Promotion.metadata_jsonb["source_url"].astext == source_url)
        parents = q.order_by(Promotion.id).all()
    corrections: dict[int, list[PromotionOverride]] = {}
    for override in db.query(PromotionOverride).filter(PromotionOverride.active.is_(True), PromotionOverride.promotion_id.in_([parent.id for parent in parents])).order_by(PromotionOverride.id):
        corrections.setdefault(override.promotion_id, []).append(override)
    variants = []
    for parent in parents:
        rows_by_key = {row.key: row for row in parent.offers}
        for data in _offers(parent):
            row = rows_by_key.get(data.key)
            try:
                data = _effective_data(parent, data, corrections.get(parent.id, []))
            except ValidationError:
                continue
            if data.publication == "retired":
                continue
            if grouping:
                member = _grouping(parent, row, data, corrections.get(parent.id, []))
                if member is None or member.key != grouping.key:
                    continue
            variant = _variant(data, first, last)
            if grouping:
                variant = variant.model_copy(update={"key": f"{parent.id}:{variant.key}"})
            variants.append(variant)
    return _serialize(p, None, variants, grouping)
