"""Conservative Spanish purchase-calendar extraction (ISO weekdays).

Pass a dedicated days field or a single offer's conditions, never an entire
multi-merchant document. Accreditation/statement dates are deliberately ignored.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from app.promotions.schemas import Evidence, Schedule

DAY_NAMES = {"lunes": 1, "martes": 2, "miercoles": 3, "jueves": 4,
             "viernes": 5, "sabado": 6, "sabados": 6, "domingo": 7, "domingos": 7}
DAY_PATTERN = r"(?:lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)"
MONTHS = {name: i for i, name in enumerate(
    ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"), 1)}
MONTHS["setiembre"] = 9


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower())
                   if not unicodedata.combining(c))


def spanish_dates(text: str) -> list[date]:
    """Only fully specified dates; never fill missing years from today's date."""
    normalized = " ".join(normalize(text).split())
    result: list[date] = []
    patterns = [
        (r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
        (r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", lambda m: (int(m[3]), int(m[2]), int(m[1]))),
        (r"\b(\d{1,2})\s+(?:de\s+)?(" + "|".join(MONTHS) + r")\s+(?:de[l]?\s+)?(\d{4})\b",
         lambda m: (int(m[3]), MONTHS[m[2]], int(m[1]))),
    ]
    found = []
    for pattern, converter in patterns:
        for match in re.finditer(pattern, normalized):
            try:
                found.append((match.start(), date(*converter(match))))
            except ValueError:
                continue
    for _, value in sorted(found):
        if value not in result:
            result.append(value)
    return result


def _listed_purchase_dates(value: str) -> Schedule | None:
    """Resolve a shared year only within an explicit, connected date list.

    Ueno's Kingo bases put the year on the final date. A missing year must
    never silently drop an earlier date, or come from today's date/URL.
    """
    pattern = (r"\b(?:(" + DAY_PATTERN + r")\s+)?(\d{1,2})\s+(?:de\s+)?("
               + "|".join(MONTHS) + r")(?:\s+(?:de[l]?\s+)?(\d{4}))?\b")
    matches = list(re.finditer(pattern, value))
    if not matches:
        return None
    if len(matches) == 1:
        match = matches[0]
        if re.search(r"\b\d{1,2}\s*(?:,\s*|y\s+|o\s+)(?:el\s+)?(?:" + DAY_PATTERN + r"\s+)?$", value[:match.start()]):
            return Schedule()  # An unparsed earlier day must not disappear.
        if not match[4]:
            return Schedule()  # A dedicated field still cannot supply a missing year.
        try:
            day = date(int(match[4]), MONTHS[match[3]], int(match[2]))
        except ValueError:
            return Schedule(state="conflict")
        if match[1] and day.isoweekday() != DAY_NAMES[match[1]]:
            return Schedule(state="conflict")
        return None  # Let the normal path retain any other fully specified dates.
    if any(
        not re.fullmatch(r"\s*(?:,\s*(?:y\s*)?|y\s*|;\s*)(?:el\s+)?", value[left.end():right.start()])
        for left, right in zip(matches, matches[1:])
    ):
        return Schedule()  # Unsupported conjunctions cannot drop partial dates.
    years = {int(match[4]) for match in matches if match[4]}
    if not years or (len(years) > 1 and any(not match[4] for match in matches)):
        return Schedule()
    if any(not match[4] for match in matches) and any(
        MONTHS[left[3]] > MONTHS[right[3]] for left, right in zip(matches, matches[1:])
    ):
        return Schedule()  # A shared year cannot resolve a December/January rollover.
    dates = []
    for match in matches:
        year = int(match[4]) if match[4] else next(iter(years))
        try:
            day = date(year, MONTHS[match[3]], int(match[2]))
        except ValueError:
            return Schedule(state="conflict")
        if match[1] and day.isoweekday() != DAY_NAMES[match[1]]:
            return Schedule(state="conflict")
        if day not in dates:
            dates.append(day)
    return Schedule(state="known", kind="specific_dates", dates=dates)


def extract_schedule(text: str, *, source_url: str | None = None,
                     dedicated: bool = False) -> Schedule:
    evidence = [Evidence(source_url=source_url, field="schedule", text=text.strip()[:1200])]
    sentences = re.split(r"[;\n]|(?<=[.!?])\s+", normalize(text))
    usable = []
    for sentence in sentences:
        if re.search(r"acredit|extracto|liquidacion|dias?\s+habiles|renova(?:cion|r).*tope", sentence):
            if not re.search(r"compras?\s+(?:realizad|efectuad)|aplica|vigente|valido", sentence):
                continue
            sentence = re.split(r"\b(?:acredit|extracto|liquidacion)", sentence)[0]
        usable.append(sentence)
    value = " ; ".join(usable).strip()
    if not value:
        return Schedule(evidence=evidence)
    ordinal_words = r"primer[oa]?|segundo|segunda|tercer[oa]?|cuarto|cuarta|quinto|quinta|ultimo|ultima"
    ordinal = re.search(r"\b(" + ordinal_words + r")\s+(" + DAY_PATTERN + r")\b", value)
    if ordinal:
        ordinals = {"primer": 1, "primero": 1, "primera": 1, "segundo": 2, "segunda": 2,
                    "tercer": 3, "tercero": 3, "tercera": 3, "cuarto": 4, "cuarta": 4,
                    "quinto": 5, "quinta": 5, "ultimo": -1, "ultima": -1}
        if "mes" not in value or len(re.findall(r"\b(?:" + ordinal_words + r")\b", value)) > 1:
            return Schedule(evidence=evidence)
        return Schedule(state="known", kind="monthly_nth_weekday",
                        weekdays=[DAY_NAMES[ordinal[2]]], ordinal=ordinals[ordinal[1]], evidence=evidence)
    monthly_range = re.search(r"\b(?:del?\s+)?(\d{1,2})\s+(?:al|a)\s+(\d{1,2})\s+de\s+cada\s+mes\b", value)
    if monthly_range:
        start, end = int(monthly_range[1]), int(monthly_range[2])
        if 1 <= start <= end <= 31:
            return Schedule(state="known", kind="monthly_day", month_days=list(range(start, end + 1)), evidence=evidence)
        return Schedule(evidence=evidence)
    monthly = re.search(r"\b(\d{1,2}(?:\s*(?:,|y)\s*\d{1,2})*)\s+de\s+cada\s+mes\b", value)
    if monthly:
        days = [int(n) for n in re.findall(r"\d+", monthly[1])]
        if all(1 <= n <= 31 for n in days):
            return Schedule(state="known", kind="monthly_day", month_days=days, evidence=evidence)
        return Schedule(evidence=evidence)
    purchase_context = dedicated or bool(re.search(
        r"(?:aplica|valida|valido|exclusivamente|unicamente)\s+(?:solo\s+)?(?:el|los)\b", value))
    # An explicit intraday window does not turn one purchase date into a
    # multi-day validity range (e.g. 'el 19 ... desde las 00:00h hasta las 23:59h').
    clock = r"(?:[01]?\d|2[0-3]):[0-5]\d"
    window = re.search(r"\bdesde\s+(?:las?\s+)?(" + clock + r")\s*h?\s+hasta\s+(?:las?\s+)?(" + clock + r")\s*h?", value)
    if window and purchase_context:
        times = [tuple(int(n) for n in time.split(":")) for time in window.groups()]
        if times[0] > times[1]:
            return Schedule(evidence=evidence)  # Overnight applicability needs an explicit end date.
    calendar_value = value[:window.start()] + value[window.end():] if window else value
    if purchase_context and not re.search(r"todos|cada\s+mes|hasta|desde|a\s+partir|vigencia", calendar_value):
        numeric = r"(?:\d{1,2}[/.]\d{1,2}[/.]\d{4}|\d{4}-\d{2}-\d{2})"
        numeric_tokens = re.finditer(r"(?<![\d/.])\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?\b", calendar_value)
        if any(not re.fullmatch(numeric, match[0]) for match in numeric_tokens):
            return Schedule(evidence=evidence)  # Never drop an earlier yearless numeric date.
        if any(not spanish_dates(match[0]) for match in re.finditer(numeric, calendar_value)):
            return Schedule(state="conflict", evidence=evidence)
        listed = _listed_purchase_dates(calendar_value)
        if listed is not None:
            return listed.model_copy(update={"evidence": evidence})
    dates = spanish_dates(calendar_value)
    mentioned_weekdays = {DAY_NAMES[m[0]] for m in re.finditer(DAY_PATTERN, value)}
    if dates and purchase_context:
        if not re.search(r"todos|cada\s+mes|hasta|desde|a\s+partir|vigencia", calendar_value):
            numeric = r"(?:\d{1,2}[/.]\d{1,2}[/.]\d{4}|\d{4}-\d{2}-\d{2})"
            for declaration in re.finditer(r"\b(" + DAY_PATTERN + r")\s+(" + numeric + r")", calendar_value):
                declared_dates = spanish_dates(declaration[2])
                if declared_dates and declared_dates[0].isoweekday() != DAY_NAMES[declaration[1]]:
                    return Schedule(state="conflict", evidence=evidence)
            if mentioned_weekdays and any(d.isoweekday() not in mentioned_weekdays for d in dates):
                return Schedule(state="conflict", evidence=evidence)
            return Schedule(state="known", kind="specific_dates", dates=dates, evidence=evidence)
    excluded = []
    exclusion_match = re.search(r"\b(?:excepto|salvo|excluyendo|no\s+aplica\s+(?:el|los))\s+(.*)", value)
    if exclusion_match:
        excluded = sorted(set(DAY_NAMES[m[0]] for m in re.finditer(DAY_PATTERN, exclusion_match[1])))
        value = value[:exclusion_match.start()]
    if re.search(r"todos\s+los\s+dias|diariamente", value):
        return Schedule(state="known", kind="all_days", excluded_weekdays=excluded, evidence=evidence)
    if re.search(r"(?:fin|fines)\s+de\s+semana", value):
        return Schedule(state="known", kind="weekly", weekdays=[6, 7], excluded_weekdays=excluded, evidence=evidence)
    if re.search(r"dias\s+de\s+semana", value):
        return Schedule(state="known", kind="weekly", weekdays=[1, 2, 3, 4, 5], excluded_weekdays=excluded, evidence=evidence)
    days = set()
    for match in re.finditer(r"(" + DAY_PATTERN + r")\s+(?:a|al|hasta)\s+(" + DAY_PATTERN + r")", value):
        start, end = DAY_NAMES[match[1]], DAY_NAMES[match[2]]
        days.update(((start - 1 + i) % 7) + 1 for i in range((end - start) % 7 + 1))
    days.update(DAY_NAMES[m[0]] for m in re.finditer(DAY_PATTERN, value))
    if days:
        if dates and not re.search(r"todos|cada|los\s+" + DAY_PATTERN, value):
            if not re.search(r"hasta|desde|vigencia", value):
                if any(d.isoweekday() not in days for d in dates):
                    return Schedule(state="conflict", evidence=evidence)
                return Schedule(state="known", kind="specific_dates", dates=dates, evidence=evidence)
        if re.search(r"\b" + DAY_PATTERN + r"\s+\d{1,2}(?:\s+(?:de\s+)?(?:" + "|".join(MONTHS) + r")\b|[/.]\d{1,2})", value) and not dates:
            return Schedule(evidence=evidence)
        return Schedule(state="known", kind="weekly", weekdays=sorted(days), excluded_weekdays=excluded, evidence=evidence)
    return Schedule(evidence=evidence)
