"""Pure, conservative field extraction shared by the bank-specific parsers."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal

from app.promotions.schemas import Benefit, Cap, Eligibility, Evidence, OfferData, Schedule
from app.scraping.utils.schedule import MONTHS, extract_schedule, normalize, spanish_dates

_MONTH_PATTERN = "|".join(MONTHS)
_OPTIONAL_WEEKDAY = r"(?:(?:lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)\s+)?"


def identity(*parts: str) -> str:
    return hashlib.sha256("|".join(normalize(p).strip() for p in parts).encode()).hexdigest()[:24]


def extract_validity(text: str) -> tuple[date | None, date | None]:
    """Extract only actual validity clauses, not unrelated posting dates."""
    value = normalize(text)
    def expand_year(match: re.Match) -> str:
        try:
            return datetime.strptime(match[0], "%d.%m.%y").strftime("%d.%m.%Y")
        except ValueError:
            return match[0]
    value = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{2}\b", expand_year, value)
    value = re.sub(r"\s+", " ", value)
    # Compact day/month/yyyy date ranges frequently used in PDF tables.
    numeric = re.search(r"(\d{1,2}[/.]\d{1,2}[/.]\d{4}|\d{4}-\d{2}-\d{2})\s*(?:hasta|al|a|[-–])\s*(?:el\s+)?(\d{1,2}[/.]\d{1,2}[/.]\d{4}|\d{4}-\d{2}-\d{2})", value)
    if numeric:
        left, right = spanish_dates(numeric[1]), spanish_dates(numeric[2])
        if left and right and left[0] <= right[0]:
            return left[0], right[0]
    # ISO "Vigencia: YYYY-MM-DD hasta YYYY-MM-DD" and Spanish dates.
    full_range = re.search(r"(?:desde(?:\s+el)?|del|a\s+partir\s+del?)\s+" + _OPTIONAL_WEEKDAY + r"(\d{1,2})\s+(?:de\s+)?(" + _MONTH_PATTERN + r")\s+(?:de[l]?\s+)?(\d{4})\s+(?:hasta|al|a)\s+(?:el\s+)?" + _OPTIONAL_WEEKDAY + r"(\d{1,2})\s+(?:de\s+)?(" + _MONTH_PATTERN + r")\s+(?:de[l]?\s+)?(\d{4})", value)
    if full_range:
        try:
            left = date(int(full_range[3]), MONTHS[full_range[2]], int(full_range[1]))
            right = date(int(full_range[6]), MONTHS[full_range[5]], int(full_range[4]))
            return (left, right) if left <= right else (None, None)
        except ValueError:
            return None, None
    shared = re.search(r"(?:desde(?:\s+el)?|del)\s+" + _OPTIONAL_WEEKDAY + r"(\d{1,2})(?:\s+(?:de\s+)?(" + _MONTH_PATTERN + r"))?\s+(?:hasta|al|a)\s+(?:el\s+)?" + _OPTIONAL_WEEKDAY + r"(\d{1,2})\s+(?:de\s+)?(" + _MONTH_PATTERN + r")\s+(?:de[l]?\s+)?(\d{4})", value)
    if shared:
        try:
            left = date(int(shared[5]), MONTHS[shared[2] or shared[4]], int(shared[1]))
            right = date(int(shared[5]), MONTHS[shared[4]], int(shared[3]))
            return (left, right) if left <= right else (None, None)
        except ValueError:
            return None, None
    end = re.search(r"(?:hasta|al)\s+(?:el\s+)?(\d{1,2}\s+de\s+(?:" + _MONTH_PATTERN + r")\s+(?:de[l]?\s+)?\d{4}|\d{1,2}[/.]\d{1,2}[/.]\d{4})", value)
    start = re.search(r"(?:desde|a\s+partir\s+de)\s+(?:el\s+)?(\d{1,2}\s+de\s+(?:" + _MONTH_PATTERN + r")\s+(?:de[l]?\s+)?\d{4})", value)
    return (spanish_dates(start[1])[0] if start and spanish_dates(start[1]) else None,
            spanish_dates(end[1])[0] if end and spanish_dates(end[1]) else None)


def extract_benefits(text: str, *, default_type: str | None = None) -> list[Benefit]:
    value = normalize(text)
    benefits: list[Benefit] = []
    percentages = list(re.finditer(r"(?<!\d)(\d{1,3}(?:[.,]\d+)?)\s*%", value))
    for index, match in enumerate(percentages):
        percentage = Decimal(match[1].replace(",", "."))
        if not 0 < percentage <= 100:
            continue
        end = percentages[index + 1].start() if index + 1 < len(percentages) else len(value)
        after = value[match.end():min(end, match.end() + 45)]
        # A descriptor cannot cross another percentage or a sentence boundary.
        # Otherwise a later financing rate inherits an earlier discount.
        before_start = percentages[index - 1].end() if index else 0
        before = value[max(before_start, match.start() - 35):match.start()]
        separator = r"[;!\n]|(?<=[.?])\s+"
        before = re.split(separator, before)[-1]
        after = re.split(separator, after)[0]
        if percentage == 100 and re.search(r"beneficio|reintegro", after) and "del" in before:
            continue
        descriptor_text = re.sub(r"\bsin\s+interes(?:es)?\b", "", after)
        keyword = re.search(r"reintegro|cashback|descuento|\boff\b|bonificacion|ahorro|recargo|interes(?:es)?|\btasa\b", descriptor_text)
        prefix = re.search(r"(reintegro|cashback|descuento|\boff\b|bonificacion|ahorro)(?:\s+(?:del?|directo|de un))?\s*$", before)
        cost_prefix = re.search(r"(?:recargo|interes(?:es)?|tasa)(?:\s+(?:del?|mensual|anual|nominal|efectiv[oa]|de interes))*\s*$", before)
        if cost_prefix or (not prefix and keyword and keyword[0] in {"recargo", "interes", "intereses", "tasa"}):
            continue
        if prefix:
            kind = "cashback" if prefix[1] in {"reintegro", "cashback"} else "discount"
        elif keyword:
            kind = "cashback" if keyword[0] in {"reintegro", "cashback"} else "discount"
        elif default_type in {"discount", "cashback"}:
            kind = default_type
        elif re.search(r"reintegro|cashback", before):
            kind = "cashback"
        elif re.search(r"descuento|off", before):
            kind = "discount"
        else:
            # A bare percentage in terms may be a surcharge or rate.
            continue
        benefit = Benefit(type=kind, percentage=percentage,
                          is_maximum=bool(re.search(r"hasta\s*$", before)))
        if benefit not in benefits:
            benefits.append(benefit)
    for match in re.finditer(r"(\d{1,3})\s+cuotas?\s+sin\s+interes(?:es)?", value):
        if 1 <= int(match[1]) <= 120:
            benefit = Benefit(type="installments", installments=int(match[1]),
                              is_maximum=bool(re.search(r"hasta\s*$", value[max(0, match.start() - 10):match.start()])))
            if benefit not in benefits:
                benefits.append(benefit)
    return benefits


def extract_eligibility(text: str) -> Eligibility:
    lower = normalize(text)
    # Do not add excluded products to the required-card list.
    # Exclusions end their own clause, not the whole document. FRIGOMAS,
    # for example, excludes web payments before the next bullet names the
    # eligible Mastercard/Visa credit cards and the Bancard POS channel.
    clauses = re.split(r"[●•➔;\n]|(?<=[.!?])\s+", lower)
    exclusion = r"se\s+excluyen|excluye|no\s+aplica\s+(?:a|para)|no\s+(?:es\s+)?valid[oa]\s+para"
    inclusion = " ".join(re.split(exclusion, clause, maxsplit=1)[0] for clause in clauses)
    cards = []
    for pattern, label in [
        (r"(?:american\s+express|amex)(?:\s+(?:platinum|gold|green))*", None),
        (r"master\s*card(?:\s+(?:duo\s+)?(?:black|clasica|oro|albirroja|metalcard|premier))*", None),
        (r"visa(?:\s+(?:infinite|signature|platinum|clasica|oro))*", None),
        (r"bancard\s+check", "Bancard Check"),
        (r"\b(?:signature|black|infinite|clasica|oro)\s+delsol\b", None),
        (r"\b(?:clasicas?|oro|black|infinite|signature)\b", None),
    ]:
        for match in re.finditer(pattern, inclusion):
            name = label or match[0].strip().title()
            name = re.sub(r"^Amex", "American Express", name)
            if label is None and any(name.casefold() in current.casefold() for current in cards):
                continue
            if name not in cards:
                cards.append(name)
    levels = list(dict.fromkeys(m[0].title() for m in re.finditer(r"nivel\s+\d+|ueno\s+(?:black|ultra|clasica)", inclusion)))
    types = [kind for word, kind in [("credito", "credit"), ("debito", "debit"), ("prepaga", "prepaid")] if word in inclusion]
    channels = [label for word, label in [("qr", "QR"), ("e-commerce", "web"), ("web", "web"), ("app", "app"), ("pos", "POS")] if re.search(r"\b" + re.escape(word) + r"\b", inclusion)]
    processors = [label for word, label in [("infonet", "Infonet"), ("bancard", "Bancard"), ("premmia", "Premmia")] if word in inclusion]
    return Eligibility(cards=cards, card_types=types, levels=levels,
                       channels=list(dict.fromkeys(channels)), processors=processors,
                       personalization_required=bool(re.search(r"personalizad|en\s+tu\s+app|segun\s+tu\s+perfil|clientes?\s+seleccionad|cupon(?:es)?\s+(?:seran\s+)?asignad|base\s+de\s+clientes\s+seleccionada", lower)),
                       conditions=[text.strip()] if text.strip() else [],
                       unknown_fields=[] if cards or types else ["cards"])


def extract_caps(text: str, *, heading: str = "") -> list[Cap]:
    value = normalize(text)
    caps = []
    for clause in re.split(r"\|\||\n|(?<=\.)\s+(?=[A-Z])", text):
        normalized = normalize(clause + " " + heading)
        if "minimo" in normalized and "tope" not in normalized and "maximo" not in normalized:
            continue
        if not re.search(r"tope|maximo|limite", normalized):
            continue
        for match in re.finditer(r"gs\.?\s*([\d.]+(?:,\d{1,2})?)", normalize(clause)):
            try:
                amount = Decimal(match[1].replace(".", "").replace(",", "."))
            except Exception:
                continue
            kind = "purchase" if "compra" in normalized else "cashback" if "reintegro" in normalized else "discount" if "descuento" in normalized else "other"
            period = next((label for token, label in [("mensual", "month"), ("mes", "month"), ("semanal", "week"), ("semana", "week"), ("diario", "day"), ("transaccion", "transaction"), ("campana", "campaign")] if token in normalized), "unknown")
            scope = "account" if "cuenta" in normalized else "card" if "por tarjeta" in normalized else "customer" if "cliente" in normalized else "unknown"
            caps.append(Cap(type=kind, amount=amount, period=period, scope=scope, description=clause.strip()))
    return caps


def make_offer(*, merchant: str, variant: str, source_url: str, text: str,
               days: str | None = None, benefits_text: str | None = None,
               card_text: str | None = None, valid_from: date | None = None,
               valid_until: date | None = None, evidence: list[Evidence] | None = None,
               default_type: str | None = None) -> OfferData:
    start, end = extract_validity(text)
    start, end = valid_from or start, valid_until or end
    schedule = extract_schedule(days if days is not None else text, source_url=source_url, dedicated=days is not None)
    if not start and not end and schedule.state == "known" and schedule.kind == "specific_dates":
        start, end = min(schedule.dates), max(schedule.dates)
    benefits = extract_benefits(benefits_text if benefits_text is not None else text, default_type=default_type)
    eligibility = extract_eligibility(card_text if card_text is not None else text)
    validity = "known" if start and end else "unknown"
    return OfferData(key=identity(merchant, variant), merchant_name=merchant[:255],
                     valid_from=start, valid_until=end, validity_state=validity,
                     schedule=schedule, benefits=benefits, eligibility=eligibility,
                     caps=extract_caps(text), evidence=evidence or [Evidence(source_url=source_url, text=text[:1800])],
                     publication="confirmed" if schedule.state == "known" and validity == "known" and benefits else "pending",
                     terms=[text] if text else [], source_url=source_url)


def legacy_summary(offers: list[OfferData]) -> tuple[str | None, int | None]:
    """Only expose a shared scalar when every variant really has the same value."""
    values = {(benefit.type, benefit.percentage) for offer in offers for benefit in offer.benefits
              if benefit.type in {"discount", "cashback"}}
    if len(values) == 1:
        kind, percentage = next(iter(values))
        return kind, int(percentage) if percentage is not None else None
    kinds = {benefit.type for offer in offers for benefit in offer.benefits}
    return next(iter(kinds)) if len(kinds) == 1 else None, None
