from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.promotions.availability import applies_on, matching_dates
from app.promotions.overrides import effective_offer, source_fingerprint
from app.promotions.schemas import Benefit, OfferData, Schedule


def offer(schedule: Schedule, **changes) -> OfferData:
    data = dict(key="merchant:variant", merchant_name="Test merchant", valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 31), validity_state="known", schedule=schedule, benefits=[Benefit(type="cashback", percentage=Decimal("20"))], publication="confirmed")
    data.update(changes)
    return OfferData(**data)


def test_additive_merchant_defaults_keep_historical_source_fingerprint():
    import hashlib
    import json

    data = offer(Schedule(state="known", kind="all_days"))
    old = data.model_dump(mode="json", exclude={"merchant", "location_scope"})
    historical = hashlib.sha256(json.dumps(old, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert source_fingerprint(old) == source_fingerprint(data) == historical
    explicit = data.model_copy(update={"location_scope": "all"})
    assert source_fingerprint(explicit) != historical


def test_expired_sunday_does_not_match_weekend():
    data = offer(Schedule(state="known", kind="weekly", weekdays=[7]), valid_until=date(2026, 10, 3))
    assert matching_dates(data, date(2026, 10, 3), date(2026, 10, 4)) == []


def test_future_offer_does_not_depend_on_scraping_status():
    data = offer(Schedule(state="known", kind="weekly", weekdays=[7]), valid_from=date(2026, 10, 4))
    assert not applies_on(data, date(2026, 10, 3))
    assert applies_on(data, date(2026, 10, 4))


def test_last_friday_is_not_every_friday():
    data = offer(Schedule(state="known", kind="monthly_nth_weekday", weekdays=[5], ordinal=-1))
    assert applies_on(data, date(2026, 10, 30))
    assert not applies_on(data, date(2026, 10, 23))


def test_monthly_day_handles_missing_day_without_rollover():
    data = offer(Schedule(state="known", kind="monthly_day", month_days=[31]), valid_from=date(2026, 2, 1), valid_until=date(2026, 3, 31))
    assert matching_dates(data, date(2026, 2, 1), date(2026, 2, 28)) == []
    assert applies_on(data, date(2026, 3, 31))


def test_exclusions_apply_after_recurrence():
    data = offer(Schedule(state="known", kind="all_days", excluded_weekdays=[1], exclusions=[date(2026, 10, 3)]))
    assert not applies_on(data, date(2026, 10, 3))
    assert not applies_on(data, date(2026, 10, 5))
    assert applies_on(data, date(2026, 10, 4))


@pytest.mark.parametrize("state", ["unknown", "conflict"])
def test_unknown_and_conflicting_rules_never_confirm(state):
    data = offer(Schedule(state=state, kind="all_days"))
    assert matching_dates(data, date(2026, 10, 1), date(2026, 10, 31)) == []


def test_missing_bounds_require_explicit_open_evidence():
    with pytest.raises(ValidationError):
        offer(Schedule(state="known", kind="all_days"), valid_until=None)
    data = offer(Schedule(state="known", kind="all_days"), valid_until=None, end_open=True)
    assert applies_on(data, date(2027, 10, 1))


def test_pending_and_retired_do_not_match():
    for publication in ("pending", "retired"):
        assert not applies_on(offer(Schedule(state="known", kind="all_days"), publication=publication), date(2026, 10, 3))


def test_nth_weekday_and_specific_dates():
    third = offer(Schedule(state="known", kind="monthly_nth_weekday", weekdays=[5], ordinal=3))
    assert matching_dates(third, date(2026, 10, 1), date(2026, 10, 31)) == [date(2026, 10, 16)]
    explicit = offer(Schedule(state="known", kind="specific_dates", dates=[date(2026, 10, 4), date(2026, 10, 8)]))
    assert matching_dates(explicit, date(2026, 10, 3), date(2026, 10, 5)) == [date(2026, 10, 4)]


def test_manual_override_preserves_original_source_data():
    from types import SimpleNamespace
    original = offer(Schedule(state="known", kind="weekly", weekdays=[7]))
    correction = SimpleNamespace(active=True, offer_key=original.key, patch_jsonb={"schedule": {"weekdays": [6]}})
    corrected = effective_offer(original, [correction])
    assert applies_on(corrected, date(2026, 10, 3))
    assert original.schedule.weekdays == [7]
