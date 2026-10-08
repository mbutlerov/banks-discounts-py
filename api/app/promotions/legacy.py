"""Read historical rows without promoting incomplete metadata to confirmation."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.promotions.schemas import Benefit, Eligibility, Evidence, OfferData, Schedule


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if isinstance(item, (str, int)) and str(item).strip()]
    return []


def legacy_offer(promotion: Any) -> OfferData:
    meta = promotion.metadata_jsonb if isinstance(promotion.metadata_jsonb, dict) else {}
    source_url = meta.get("source_url") if isinstance(meta.get("source_url"), str) else None
    start, end = promotion.start_date, promotion.end_date
    contradictory = bool(start and end and start > end)
    evidence = [Evidence(source_url=source_url, text="Historical extraction: conditions require revalidation", method="legacy")]
    if contradictory:
        evidence.append(Evidence(field="validity", text=f"Conflicting historical bounds: {start} through {end}", method="legacy"))
        start, end = None, None
    benefits = []
    if promotion.benefit_type in {"discount", "cashback", "installments", "points", "other"}:
        try:
            value = Decimal(str(promotion.discount_percentage)) if promotion.discount_percentage is not None else None
            value = value if value is None or 0 <= value <= 100 else None
        except (ValueError, InvalidOperation):
            value = None
        installments = meta.get("installments")
        try:
            installments = int(installments) if installments is not None else None
            if installments is not None and not 1 <= installments <= 120:
                installments = None
        except (TypeError, ValueError):
            installments = None
        benefits = [Benefit(type=promotion.benefit_type, percentage=value, installments=installments)]
    return OfferData(
        key=f"legacy:{promotion.id}", merchant_name=promotion.title,
        valid_from=start, valid_until=end,
        validity_state="conflict" if contradictory else "unknown", schedule=Schedule(state="unknown"),
        benefits=benefits,
        eligibility=Eligibility(cards=string_list(meta.get("applicable_cards")), unknown_fields=["schedule", "validity"]),
        publication="pending", source_url=source_url,
        evidence=evidence,
    )
