from __future__ import annotations

from app.scraping.utils.schedule import extract_schedule

_ALL_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def extract_valid_days(text: str) -> list[str] | None:
    """Legacy projection: monthly/date/unknown rules must not become weekly."""
    rule = extract_schedule(text)
    if rule.state != "known" or rule.kind not in {"weekly", "all_days"}:
        return None
    weekdays = range(1, 8) if rule.kind == "all_days" else rule.weekdays
    return [_ALL_DAYS[d - 1] for d in weekdays if d not in rule.excluded_weekdays]
