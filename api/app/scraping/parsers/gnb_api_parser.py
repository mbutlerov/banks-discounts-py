"""Interpret one verified GNB API record, never a listing or related benefits.

The API embeds the bank's detail HTML. Its benefit bullets, rather than the
promotional maximum in the heading, establish the individual card variants.
"""
from __future__ import annotations

import json
import re
from datetime import date

from bs4 import BeautifulSoup

from app.promotions.schemas import Evidence, MerchantLocation, OfferData, Schedule
from app.scraping.clients.pdf_reader import PdfReaderError
from app.scraping.parsers.pdf_offers import read_pdf
from app.scraping.schemas import ScrapedPromotion, ScrapedSource
from app.scraping.utils.categorizer import categorize
from app.scraping.utils.offers import extract_benefits, extract_caps, extract_eligibility, extract_validity, identity, legacy_summary, make_offer
from app.scraping.utils.schedule import DAY_NAMES, DAY_PATTERN, MONTHS, extract_schedule, normalize


def _iso(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _calendar(text: str, url: str) -> Schedule:
    """Support the portal's explicit 'miércoles 7 y 14 de octubre del 2026'."""
    value = normalize(text)
    match = re.search(r"\b(" + DAY_PATTERN + r")\s+(\d{1,2}(?:\s*(?:,|y)\s*\d{1,2})+)\s+(?:de\s+)?(" + "|".join(MONTHS) + r")\s+(?:del?\s+)?(\d{4})\b", value)
    if match:
        try:
            dates = [date(int(match[4]), MONTHS[match[3]], int(day)) for day in re.findall(r"\d+", match[2])]
        except ValueError:
            return Schedule(state="conflict")
        if any(day.isoweekday() != DAY_NAMES[match[1]] for day in dates):
            return Schedule(state="conflict")
        return Schedule(state="known", kind="specific_dates", dates=dates,
                        evidence=[Evidence(source_url=url, field="schedule", text=text)])
    return extract_schedule(text, source_url=url, dedicated=True)


def _schedule_for(clause: str, introductions: list[str], kind: str, url: str) -> Schedule:
    own = _calendar(clause, url)
    candidates: list[Schedule] = []
    for intro in introductions:
        # A mixed heading explicitly establishes two purchase calendars, e.g.
        # discount on Thursdays and financing every day. Scope each side first.
        parts = re.split(r"\s*\+\s*(?=(?:hasta\s+)?\d+\s+cuotas?)", intro, flags=re.I)
        found_for_kind = False
        for part in parts:
            benefits = extract_benefits(part)
            if kind == "installments" and not any(b.type == "installments" for b in benefits):
                continue
            if kind != "installments" and benefits and all(b.type == "installments" for b in benefits):
                continue
            schedule = _calendar(part, url)
            if schedule.state != "unknown":
                candidates.append(schedule)
                found_for_kind = True
        if not found_for_kind and len(parts) > 1:
            # In '20% + 6 cuotas los sábados' the trailing calendar applies
            # to the complete compound offer. An earlier explicit calendar
            # would have selected the appropriate part above instead.
            calendar = _calendar(intro, url)
            if calendar.state != "unknown":
                candidates.append(calendar)
    # An installment bullet with its own explicit calendar is authoritative
    # even when the heading combines cash benefits and financing.
    if own.state != "unknown":
        if any(schedule.model_dump(exclude={"evidence"}) != own.model_dump(exclude={"evidence"})
               for schedule in candidates):
            return Schedule(state="conflict", evidence=own.evidence + [e for s in candidates for e in s.evidence])
        return own
    unique = {(s.state, s.kind, tuple(s.weekdays), tuple(s.month_days), tuple(s.dates), s.ordinal) for s in candidates}
    if len(unique) > 1:
        return Schedule(state="conflict", evidence=[e for s in candidates for e in s.evidence])
    return candidates[0] if candidates else Schedule()


def _eligible(text: str):
    eligibility = extract_eligibility(text)
    # These named GNB products are missing from the generic bank vocabulary.
    # This is limited to this bullet's positive eligibility statement.
    lower = normalize(text)
    extra = [(r"\bblack\s+premier\b", "Mastercard Black Premier"),
             (r"\bmetalcard(?:\s+premier)?\b", "Mastercard Metalcard Premier"),
             (r"\bvisa\s+infinite\s+premier\b", "Visa Infinite Premier")]
    for pattern, name in extra:
        if re.search(pattern, lower) and name not in eligibility.cards:
            eligibility.cards.append(name)
    return eligibility


def _location(clause: str, url: str) -> list[MerchantLocation]:
    match = re.search(r"\b(?:en\s+la\s+)?sucursal\s+de\s+([^.;]+)", clause, re.I)
    if not match:
        return []
    city = match[1].strip()
    if len(city) > 100:
        return []
    return [MerchantLocation(key=identity("gnb", "city-branch", city), name="Sucursal de " + city,
                             city=city, evidence=[Evidence(source_url=url, field="location", text=clause)])]


def _variant(clause: str, kind: str) -> str:
    # Stable under a percentage change, and independent of a row's position.
    condition = re.sub(r"\b\d+(?:[.,]\d+)?\s*%", "", normalize(clause))
    condition = re.sub(r"\b(?:hasta|de\s+descuento|de\s+reintegro|off)\b", "", condition)
    return "gnb-api:" + kind + ":" + " ".join(condition.split())


def _scoped_caps(clauses: list[str], eligibility) -> tuple[list, bool]:
    caps = []
    ambiguous = False
    for clause in clauses:
        # A purchase ceiling and its equivalent cashback ceiling are two
        # different monetary limits even when GNB prints them in one bullet.
        for part in re.split(r"(?=\bequivalente\s+a\s+un\s+reintegro\b)", clause, flags=re.I):
            parsed = extract_caps(part)
            if not parsed:
                continue
            qualified = _eligible(part)
            products = {normalize(card) for card in qualified.cards}
            offer_products = {normalize(card) for card in eligibility.cards}
            if ((products and products != offer_products)
                    or (qualified.card_types and set(qualified.card_types) != set(eligibility.card_types))):
                ambiguous = True
                continue
            # Several differently priced products in a single cap statement
            # cannot be interpreted by copying every amount to every tier.
            if products and len({cap.amount for cap in parsed}) > 1:
                ambiguous = True
                continue
            caps.extend(parsed)
    return caps, ambiguous


def _validity(record: dict, raw_text: str, introductions: list[str], url: str):
    start, end = extract_validity(raw_text)
    ranges = {extract_validity(intro) for intro in introductions if any(extract_validity(intro))}
    conflict = len(ranges) > 1
    for intro in introductions:
        normalized = normalize(intro)
        if re.match(r"\s*(?:del|desde)\s+\d", normalized) and re.search(r"\b(?:" + "|".join(MONTHS) + r")\b", normalized):
            left, right = extract_validity(intro)
            if not left or not right:
                # A malformed/impossible narrated interval is not repaired
                # by an unrelated administrative startDate from the API.
                conflict = True
    # A one-day promotion states its validity through its explicit dated
    # purchase calendar, not through a broad administrative API interval.
    if not start and not end:
        for intro in introductions:
            schedule = _calendar(intro, url)
            if schedule.state == "known" and schedule.kind == "specific_dates":
                start, end = min(schedule.dates), max(schedule.dates)
                break
    api_start, api_end = _iso(record.get("startDate")), _iso(record.get("endDate"))
    if record.get("startDate") and not api_start or record.get("endDate") and not api_end:
        conflict = True
    if api_start and api_end and api_start > api_end:
        conflict = True
        api_start = api_end = None
    if start and api_start and start != api_start or end and api_end and end != api_end:
        conflict = True
    start, end = start or api_start, end or api_end
    if start and end and start > end:
        start = end = None
        conflict = True
    evidence = [Evidence(source_url=url, field="validity", text=f"API startDate={record.get('startDate')}; endDate={record.get('endDate')}")]
    evidence.extend(Evidence(source_url=url, field="validity", text=intro) for intro in introductions
                    if any(extract_validity(intro)) or _calendar(intro, url).kind == "specific_dates")
    return start, end, conflict, evidence


def _parse_json_offers(record: dict, source: ScrapedSource) -> tuple[list[OfferData], str, list[str]]:
    soup = BeautifulSoup(record.get("description") or "", "html.parser")
    raw_text = "\n".join(" ".join(element.get_text(" ", strip=True).split()) for element in soup.find_all(["p", "li"]))
    introductions = [" ".join(p.get_text(" ", strip=True).split()) for p in soup.find_all("p")
                     if not p.find_parent("li") and p.get_text(strip=True)]
    bullet_texts = [" ".join(li.get_text(" ", strip=True).split()) for li in soup.find_all("li")
                   if not li.find_parent("li")]
    # The same verified portal emits the base and the explicit QR increment
    # either in two bullets or within one bullet, depending on the record.
    clauses = [part.strip() for bullet in bullet_texts for part in re.split(
        r"(?=\+\s*\d+(?:[.,]\d+)?\s*%\s*(?:de\s+)?(?:reintegro|descuento)\s+adicional\b)",
        bullet, flags=re.I) if part.strip()]
    url = source.metadata.get("api_url") or source.source_url
    merchant = str(record["title"]).strip()
    start, end, validity_conflict, validity_evidence = _validity(record, raw_text, introductions, url)
    warnings = ["gnb_api_validity_conflict"] if validity_conflict else []
    benefits_by_clause = [(clause, extract_benefits(clause)) for clause in clauses]
    shared = [clause for clause, benefits in benefits_by_clause if not benefits]
    introductions.extend(clause for clause in shared if re.search(r"\bla\s+promocion\s+aplica\b", normalize(clause))
                         and "cuotas" not in normalize(clause))
    # Exclusions and accreditation references remain visible in the terms;
    # they never add eligible cards/channels to another benefit's variant.
    payment_context = "\n".join(clause for clause in shared if re.search(
        r"pagos|compras|exclusivamente|procesadora|\bpos\b", normalize(clause))
        and not re.search(r"no\s+(?:aplica|participan)|se\s+excluyen|reembolso|reflejad|visualizar|extracto", normalize(clause)))
    shared_eligibility = extract_eligibility(payment_context)
    offers: list[OfferData] = []
    additions: list[tuple[str, list]] = []
    for clause, benefits in benefits_by_clause:
        if not benefits:
            continue
        if re.search(r"\badicional\b", normalize(clause)):
            additions.append((clause, benefits))
            continue
        # A cap, percentage rate, minimum or excluded product is not a new
        # benefit bullet even if its prose mentions the word descuento.
        if re.match(r"(?:tope|ticket|minimo|no\s+(?:aplica|participan)|se\s+excluyen)", normalize(clause)):
            continue
        for kind in dict.fromkeys(b.type for b in benefits):
            selected = [b for b in benefits if b.type == kind]
            offer = make_offer(merchant=merchant, variant=_variant(clause, kind), source_url=source.source_url,
                               text=raw_text, days="", benefits_text="", card_text=clause,
                               valid_from=start, valid_until=end,
                               evidence=[Evidence(source_url=url, field="benefit", section="description/li", text=clause), *validity_evidence])
            offer.benefits = selected
            offer.schedule = _schedule_for(clause, introductions, kind, url)
            offer.eligibility = _eligible(clause)
            for name in ("channels", "processors"):
                current = getattr(offer.eligibility, name)
                current.extend(value for value in getattr(shared_eligibility, name) if value not in current)
            offer.eligibility.conditions = [clause, *shared]
            offer.caps, cap_conflict = ([], False) if kind == "installments" else _scoped_caps(shared, offer.eligibility)
            if cap_conflict:
                offer.eligibility.unknown_fields.append("caps")
                warnings.append("gnb_api_cap_scope_unresolved")
            offer.locations = _location(clause, url)
            if offer.locations:
                offer.location_scope = "specified"
                offer.eligibility.cities = [location.city for location in offer.locations if location.city]
            if validity_conflict:
                offer.validity_state = "conflict"
            offer.publication = "confirmed" if (not validity_conflict and start and end
                and offer.schedule.state == "known" and len(selected) == 1 and not cap_conflict
                and offer.eligibility.card_types) else "pending"
            if len(selected) != 1:
                warnings.append("gnb_api_ambiguous_benefit_bullet")
            offers.append(offer)
    for clause, benefits in additions:
        eligible = _eligible(clause)
        candidates = [offer for offer in offers if len(offer.benefits) == 1
                      and offer.benefits[0].type != "installments"
                      and offer.eligibility.card_types == eligible.card_types
                      and (not eligible.cards or offer.eligibility.cards == eligible.cards)]
        safe = len(candidates) == 1 and len(benefits) == 1 and "QR" in eligible.channels
        if safe:
            base = candidates[0]
            extra = benefits[0]
            benefit = base.benefits[0]
            extra_schedule = _schedule_for(clause, introductions, extra.type, url)
            safe = (benefit.type == extra.type and benefit.percentage is not None and extra.percentage is not None
                    and benefit.percentage + extra.percentage <= 100
                    and extra_schedule.model_dump(exclude={"evidence"}) == base.schedule.model_dump(exclude={"evidence"}))
        if safe:
            offer = base.model_copy(deep=True)
            offer.key = identity(merchant, _variant(clause, extra.type))
            offer.benefits[0].percentage += extra.percentage
            offer.benefits[0].conditions = [base.evidence[0].text, clause]
            offer.eligibility = eligible
            offer.eligibility.unknown_fields.extend(field for field in base.eligibility.unknown_fields
                                                   if field not in offer.eligibility.unknown_fields)
            offer.eligibility.cities = base.eligibility.cities.copy()
            offer.eligibility.locations = base.eligibility.locations.copy()
            offer.eligibility.conditions = [base.evidence[0].text, clause, *shared]
            offer.evidence.append(Evidence(source_url=url, field="benefit", section="description/li", text=clause))
            offers.append(offer)
        else:
            warnings.append("gnb_api_additional_benefit_unresolved")
            offer = make_offer(merchant=merchant, variant=_variant(clause, "additional"), source_url=source.source_url,
                               text=raw_text, benefits_text=clause, card_text=clause, days="",
                               valid_from=start, valid_until=end,
                               evidence=[Evidence(source_url=url, field="benefit", text=clause), *validity_evidence])
            offer.schedule = _schedule_for(clause, introductions, benefits[0].type, url)
            offer.publication = "pending"
            if validity_conflict:
                offer.validity_state = "conflict"
            offers.append(offer)
    if not offers:
        # A promotional heading alone cannot establish the detailed eligibility
        # associated with a maximum, amount-based discount or undefined benefit.
        offer = make_offer(merchant=merchant, variant="gnb-api:unrecognized", source_url=source.source_url,
                           text=raw_text, days="", benefits_text="", valid_from=start, valid_until=end,
                           evidence=validity_evidence)
        offer.publication = "pending"
        offers = [offer]
        warnings.append("gnb_api_benefit_details_unrecognized")
    by_key: dict[str, OfferData] = {}
    unique_offers = []
    for offer in offers:
        previous = by_key.get(offer.key)
        if previous:
            if previous.model_dump() == offer.model_dump():
                continue  # Repeated identical evidence adds no new variant.
            previous.publication = offer.publication = "pending"
            offer.key = identity(merchant, offer.key, "duplicate", offer.evidence[0].text, str(len(unique_offers)))
            warnings.append("gnb_api_duplicate_variant")
        else:
            by_key[offer.key] = offer
        unique_offers.append(offer)
    offers = unique_offers
    # Inactive records can remain useful historical evidence, but they cannot
    # become current confirmed promotions because their dates overlap today.
    if record.get("status") is False:
        for offer in offers:
            offer.publication = "retired"
    elif record.get("status") is not True:
        for offer in offers:
            offer.publication = "pending"
        warnings.append("gnb_api_status_unrecognized")
    return offers, raw_text, warnings


def parse_gnb_json(source: ScrapedSource, parse_pdf) -> list[ScrapedPromotion]:
    try:
        payload = json.loads(source.text or "")
    except (ValueError, TypeError):
        source.metadata["parse_warning"] = "gnb_api_invalid_json"
        source.metadata["offers_complete"] = "false"
        return []
    record = payload.get("data") if isinstance(payload, dict) else None
    if (not isinstance(record, dict) or not record.get("id") or not isinstance(record.get("title"), str)
            or not record["title"].strip() or not isinstance(record.get("description", ""), (str, type(None)))):
        source.metadata["parse_warning"] = "gnb_api_requires_single_detail"
        source.metadata["offers_complete"] = "false"
        return []
    if source.metadata.get("parse_warning", "").startswith("gnb_"):
        source.metadata.pop("parse_warning", None)
    offers, raw_text, warnings = _parse_json_offers(record, source)
    merchant = record["title"].strip()
    unreadable = False
    for document in source.documents:
        if document.source_type != "pdf":
            continue
        try:
            pages = read_pdf(document)
        except PdfReaderError:
            unreadable = True
            warnings.append("gnb_pdf_unreadable")
            continue
        text = "\n".join(page.text for page in pages)
        if document.metadata.get("role") == "adherents":
            # This attachment describes participating merchants/locations;
            # unrelated dates or percentages inside it are not legal terms
            # establishing the campaign's validity and benefit.
            for offer in offers:
                offer.evidence.append(Evidence(source_url=document.source_url, page=pages[0].number if pages else None,
                                               field="adherents", text=text[:1800]))
                if text:
                    offer.terms.append(text)
            continue
        pdf_offers = parse_pdf(pages, document.source_url, merchant)
        if not pdf_offers:
            warnings.append("gnb_pdf_template_unrecognized")
        pdf_start, pdf_end = extract_validity(text)
        for offer in offers:
            offer.evidence.append(Evidence(source_url=document.source_url, page=pages[0].number if pages else None,
                                           field="legal_terms", text=text[:1800]))
            if text:
                offer.terms.append(text)
            if (pdf_start and offer.valid_from and pdf_start != offer.valid_from
                    or pdf_end and offer.valid_until and pdf_end != offer.valid_until):
                offer.validity_state = "conflict"
                offer.publication = "pending"
                warnings.append("gnb_pdf_validity_conflict")
            if pdf_offers:
                matching = [pdf_offer for pdf_offer in pdf_offers
                            if set(pdf_offer.eligibility.card_types) == set(offer.eligibility.card_types)
                            and {normalize(card) for card in pdf_offer.eligibility.cards} == {normalize(card) for card in offer.eligibility.cards}
                            and set(pdf_offer.eligibility.channels) == set(offer.eligibility.channels)]
                if not matching:
                    offer.publication = "pending"
                    warnings.append("gnb_pdf_eligibility_unresolved")
                    continue
                signatures = {tuple((b.type, b.percentage, b.installments) for b in pdf_offer.benefits) for pdf_offer in matching}
                actual = tuple((b.type, b.percentage, b.installments) for b in offer.benefits)
                if signatures != {actual}:
                    offer.publication = "pending"
                    warnings.append("gnb_pdf_benefit_conflict")
                if any(pdf_offer.schedule.state == "known" and offer.schedule.state == "known"
                       and pdf_offer.schedule.model_dump(exclude={"evidence"}) != offer.schedule.model_dump(exclude={"evidence"})
                       for pdf_offer in matching):
                    offer.publication = "pending"
                    warnings.append("gnb_pdf_schedule_conflict")
    if unreadable or source.metadata.get("document_error"):
        for offer in offers:
            if offer.publication != "retired":
                offer.publication = "pending"
    if record.get("status") is False:
        for offer in offers:
            offer.publication = "retired"
    if warnings:
        source.metadata["parse_warning"] = ",".join(dict.fromkeys(warnings))
    source.metadata["offers_complete"] = "false" if warnings or source.metadata.get("document_error") else "true"
    kind, percentage = legacy_summary(offers)
    category_hint = (record.get("category") or {}).get("name") if isinstance(record.get("category"), dict) else None
    category = categorize(merchant, raw_text, category_hint=category_hint)
    return [ScrapedPromotion(bank_slug="gnb", title=merchant, merchant_name=merchant,
                             source_key="gnb:" + str(record["id"]), category_name=category,
                             description=raw_text[:2000], raw_text=raw_text,
                             start_date=offers[0].valid_from, end_date=offers[0].valid_until,
                             benefit_type=kind, discount_percentage=percentage, offers=offers,
                             metadata={**source.metadata, "source_url": source.source_url})]
