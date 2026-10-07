"""One calendar evaluator shared by indexing, persistence and public queries."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

from app.promotions.schemas import OfferData

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.database.models.offer import PromotionOffer


PRODUCT_TIMEZONE = ZoneInfo("America/Asuncion")
MAX_QUERY_DAYS = 31
MAX_EVALUATION_DAYS = 370


def today_local() -> date:
    from datetime import datetime
    return datetime.now(PRODUCT_TIMEZONE).date()


def availability_state(offer: OfferData) -> Literal["confirmed", "unknown", "conflict", "not_applicable"]:
    if offer.publication == "retired":
        return "not_applicable"
    if offer.schedule.state == "conflict" or offer.validity_state == "conflict":
        return "conflict"
    if offer.publication != "confirmed" or offer.schedule.state != "known" or offer.validity_state != "known" or not offer.benefits:
        return "unknown"
    return "confirmed"


def applies_on(offer: OfferData, selected: date) -> bool:
    if availability_state(offer) != "confirmed":
        return False
    if offer.valid_from and selected < offer.valid_from:
        return False
    if offer.valid_until and selected > offer.valid_until:
        return False
    rule = offer.schedule
    if selected in rule.exclusions or selected.isoweekday() in rule.excluded_weekdays:
        return False
    if rule.kind == "all_days":
        return True
    if rule.kind == "weekly":
        return selected.isoweekday() in rule.weekdays
    if rule.kind == "monthly_day":
        return selected.day in rule.month_days
    if rule.kind == "specific_dates":
        return selected in rule.dates
    if rule.kind == "monthly_nth_weekday":
        if selected.isoweekday() not in rule.weekdays:
            return False
        if rule.ordinal == -1:
            return selected.day + 7 > calendar.monthrange(selected.year, selected.month)[1]
        return (selected.day - 1) // 7 + 1 == rule.ordinal
    return False


def matching_dates(offer: OfferData, date_from: date, date_to: date, weekday: int | None = None) -> list[date]:
    if date_to < date_from:
        raise ValueError("date_to must not be before date_from")
    if (date_to - date_from).days >= MAX_EVALUATION_DAYS:
        raise ValueError(f"evaluation range cannot exceed {MAX_EVALUATION_DAYS} days")
    if availability_state(offer) != "confirmed":
        return []
    return [
        selected
        for offset in range((date_to - date_from).days + 1)
        if (selected := date_from + timedelta(days=offset))
        and (weekday is None or selected.isoweekday() == weekday)
        and applies_on(offer, selected)
    ]


def regenerate_occurrences(db: Session, offer: PromotionOffer, data: OfferData, localdate: date | None = None, horizon: int = 90) -> int:
    """Replace rules and coverage in the caller's transaction; never commit.

    Calculate/validate before deleting, so malformed rules preserve the previous
    index. The enclosing transaction rolls all changes back on database failures.
    Queries outside coverage use the same pure evaluator, never a false empty set.
    """
    from sqlalchemy import delete
    from app.database.models.offer import OfferOccurrence

    if horizon < 1 or horizon > MAX_EVALUATION_DAYS:
        raise ValueError(f"horizon must be between 1 and {MAX_EVALUATION_DAYS}")
    first = localdate or today_local()
    last = first + timedelta(days=horizon - 1)
    dates = matching_dates(data, first, last)
    db.flush()
    db.execute(delete(OfferOccurrence).where(OfferOccurrence.offer_id == offer.id))
    db.add_all([OfferOccurrence(offer_id=offer.id, applies_on=selected, rules_version=offer.version) for selected in dates])
    offer.coverage_from = first
    offer.coverage_until = last
    offer.occurrences_version = offer.version
    db.flush()
    return len(dates)
