from __future__ import annotations

import json
import re
from datetime import date, timedelta
from decimal import Decimal

from bs4 import BeautifulSoup

from app.promotions.schemas import Benefit, Evidence, Schedule
from app.scraping.base import BaseParser
from app.scraping.schemas import ScrapedPromotion, ScrapedSource
from app.scraping.clients.pdf_reader import PdfReaderError
from app.scraping.parsers.pdf_offers import read_pdf
from app.scraping.sources.bank_documents import attachment_mime, official_document_url
from app.scraping.utils.offers import extract_benefits, extract_caps, extract_eligibility, extract_validity, identity, legacy_summary, make_offer
from app.scraping.utils.schedule import MONTHS, normalize


class AtlasHtmlParser(BaseParser):
    def parse(self, source: ScrapedSource) -> list[ScrapedPromotion]:
        soup = BeautifulSoup(source.text or "", "html.parser")
        card = soup.select_one(".benefit-card[data-nombre]")
        if card is None:
            return []
        get = lambda field: str(card.get("data-" + field, "")).strip()
        merchant, terms, days = get("nombre"), get("terminos"), get("dias")
        if not merchant:
            return []
        rows = json.loads(source.metadata.get("jsonld", "[]"))
        structured = rows[0] if len(rows) == 1 else {}
        def parse_date(value):
            try:
                return date.fromisoformat(value)
            except (TypeError, ValueError):
                return None
        ld_start, ld_end = parse_date(structured.get("validFrom")), parse_date(structured.get("validThrough"))
        term_start, term_end = extract_validity(terms)
        start, end = term_start or ld_start, term_end or ld_end
        conflict = bool((term_start and ld_start and term_start != ld_start) or (term_end and ld_end and term_end != ld_end) or len(rows) > 1)
        evidence = [Evidence(source_url=source.source_url, field="validity", text=terms),
                    Evidence(source_url=source.source_url, field="validity", method="jsonld", text=json.dumps(rows, ensure_ascii=False)),
                    Evidence(source_url=source.source_url, field="schedule", text=days)]
        base_text = get("pct") + " " + get("label-pct") + " " + get("cuotas")
        base = make_offer(merchant=merchant, variant="base", source_url=source.source_url,
                          text=terms, days=days, benefits_text=base_text, card_text=get("desc"),
                          valid_from=start, valid_until=end, evidence=evidence)
        # The dedicated days field can name a single event or a date range
        # without its year. Resolve it only inside one official year/window.
        # JSON-LD frequently starts on the first of the catalogue month: that
        # is the validity window, not an instruction to apply every day.
        event_schedule = _dated_event_schedule(days, start, end, evidence)
        if event_schedule is not None:
            base.schedule = event_schedule
            if event_schedule.state == "conflict":
                conflict = True
        full_eligibility = extract_eligibility(terms)
        base.eligibility.processors = full_eligibility.processors
        base.eligibility.channels = full_eligibility.channels
        base.eligibility.conditions = [get("desc"), terms]
        for field in ["ciudades"]:
            try:
                base.eligibility.cities = json.loads(get(field) or "[]")
            except (ValueError, TypeError):
                pass
        if "en sucursales" in normalize(get("extra-resto")):
            base.eligibility.locations = [get("extra-resto")]
        cap_rows = [line for line in get("topes").split("||") if re.search(r"gs\.?\s*\d", normalize(line)) and "minimo" not in normalize(line)]
        minimum_lines = [line for line in get("topes").split("||") if "minimo" in normalize(line)]
        base.eligibility.conditions.extend(line for line in minimum_lines if line not in base.eligibility.conditions)
        base.evidence.extend(Evidence(source_url=source.source_url, field="minimum_purchase", text=line) for line in minimum_lines)
        variants = []
        extra_benefits = extract_benefits(get("extra-label"))
        extra_eligibility = extract_eligibility(get("extra-resto"))
        # Each cap row identifies its own eligible card tier; don't take max.
        if cap_rows:
            for line in cap_rows:
                offer = base.model_copy(deep=True)
                cap_eligibility = extract_eligibility(line)
                offer.key = identity(merchant, "cap:" + ",".join(cap_eligibility.cards or cap_eligibility.card_types))
                if cap_eligibility.cards:
                    offer.eligibility.cards = cap_eligibility.cards
                offer.caps = extract_caps(line, heading=get("topes-titulo"))
                for cap in offer.caps:
                    if "por cuenta de tarjeta" in normalize(terms):
                        cap.scope = "account"
                offer.evidence.append(Evidence(source_url=source.source_url, field="cap", text=get("topes-titulo") + ": " + line))
                offer.eligibility.conditions.append(line)
                # A card-specific extra only applies to the matching tier.
                if extra_benefits and extra_eligibility.cards:
                    labels = normalize(" ".join(cap_eligibility.cards))
                    tokens = re.findall(r"black|infinite|signature|platinum|delsol", normalize(get("extra-resto")))
                    if tokens and any(token in labels for token in tokens):
                        offer = _add_extra(offer, extra_benefits, get("extra-resto"))
                    elif not cap_eligibility.cards:
                        # Shared cap: preserve the baseline card and the special
                        # extra as separate variants with the same cap.
                        special = _add_extra(offer.model_copy(deep=True), extra_benefits, get("extra-resto"))
                        special.key = identity(merchant, "extra:" + get("extra-resto"))
                        special.eligibility.cards = extra_eligibility.cards
                        variants.append(special)
                elif extra_benefits and not extra_eligibility.cards:
                    offer = _add_extra(offer, extra_benefits, get("extra-resto"))
                variants.append(offer)
        else:
            variants = [base]
            if extra_benefits:
                extra = _add_extra(base.model_copy(deep=True), extra_benefits, get("extra-resto"))
                extra.key = identity(merchant, "extra:" + get("extra-resto"))
                extra.eligibility.cards = extra_eligibility.cards
                if extra_eligibility.card_types:
                    extra.eligibility.card_types = extra_eligibility.card_types
                variants.append(extra)
        for offer in variants:
            if conflict:
                offer.validity_state, offer.publication = "conflict", "pending"
            elif offer.validity_state == "known" and offer.schedule.state == "known" and offer.benefits:
                offer.publication = "confirmed"
            if get("expired").lower() == "true":
                offer.publication = "retired"
        if len({o.key for o in variants}) != len(variants):
            source.metadata["parse_warning"] = "atlas_unresolved_cap_variants"
            for offer in variants:
                offer.publication = "pending"
        _apply_terms_documents(source, variants, get("boton-url"), get("boton-texto"))
        _apply_adherent_documents(source, variants, get("boton-url"), get("boton-texto"))
        kind, percentage = legacy_summary(variants)
        try:
            categories = json.loads(get("categoria") or "[]")
        except ValueError:
            categories = []
        metadata = {**source.metadata, "source_url": source.source_url, "source_categories": " | ".join(categories)}
        metadata["offers_complete"] = "true" if variants and all(o.benefits for o in variants) and len({o.key for o in variants}) == len(variants) and not source.metadata.get("document_error") and not source.metadata.get("parse_warning") else "false"
        if "pdf_url" in source.metadata:
            metadata["pdf_url"] = source.metadata["pdf_url"]
        return [ScrapedPromotion(bank_slug="atlas", title=merchant, merchant_name=merchant,
                                 category_name=categories[0] if categories else None,
                                 description=base_text, terms_summary=terms[:1500], raw_text=terms,
                                 start_date=start, end_date=end, benefit_type=kind, discount_percentage=percentage,
                                 source_key=source.metadata.get("source_key") or "atlas:card:" + identity(merchant),
                                 offers=variants, metadata=metadata)]


def _dated_event_schedule(days: str, start: date | None, end: date | None,
                          evidence: list[Evidence]) -> Schedule | None:
    if not start or not end or start.year != end.year or start > end:
        return None
    value = " ".join(normalize(days).split())
    months = "|".join(MONTHS)
    numeric = re.fullmatch(r"(?:del?\s+)?(\d{1,2})/(\d{1,2})\s+(?:al|a)\s+(\d{1,2})/(\d{1,2})", value)
    written = re.fullmatch(r"(?:del?\s+)?(\d{1,2})(?:\s+de\s+(" + months + r"))?\s+(?:al|a)\s+(\d{1,2})\s+de\s+(" + months + r")", value)
    single = re.fullmatch(r"(?:el\s+)?(\d{1,2})\s+de\s+(" + months + r")", value)
    if not (numeric or written or single):
        return None
    schedule_evidence = [item.model_copy(deep=True) for item in [evidence[2], evidence[0], evidence[1]]]
    try:
        if numeric:
            first = date(start.year, int(numeric[2]), int(numeric[1]))
            last = date(start.year, int(numeric[4]), int(numeric[3]))
        elif written:
            first = date(start.year, MONTHS[written[2] or written[4]], int(written[1]))
            last = date(start.year, MONTHS[written[4]], int(written[3]))
        else:
            first = last = date(start.year, MONTHS[single[2]], int(single[1]))
    except ValueError:
        return Schedule(state="conflict", evidence=schedule_evidence)
    if not start <= first <= last <= end:
        return Schedule(state="conflict", evidence=schedule_evidence)
    dates = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    return Schedule(state="known", kind="specific_dates", dates=dates, evidence=schedule_evidence)


def _add_extra(offer, extra_benefits: list[Benefit], condition: str):
    for extra in extra_benefits:
        existing = next((b for b in offer.benefits if b.type == extra.type and b.percentage is not None), None)
        if existing and extra.percentage is not None and existing.percentage + extra.percentage <= 100:
            existing.percentage += extra.percentage
            existing.conditions.append(condition)
        else:
            extra = extra.model_copy(deep=True)
            extra.conditions.append(condition)
            offer.benefits.append(extra)
    offer.eligibility.conditions.append(condition)
    return offer


def _apply_adherent_documents(source, variants, button_url: str, button_label: str) -> None:
    """Bind campaign conditions to its official adherent list, never all shops.

    A scanned/image annex is acquired and linked, but remains unknown until
    locally extracted. Only recognized table headings yield named locations.
    """
    if "adherid" not in normalize(button_label):
        return
    url = official_document_url(button_url, source.source_url, "bancoatlas.com.py")
    if not url:
        return
    document = next((doc for doc in source.documents if doc.source_url == url), None)
    locations = []
    evidence = [Evidence(source_url=url, field="merchant_locations", text=button_label, method="official-annex-link")]
    if document is not None and document.source_type == "pdf":
        try:
            pages = read_pdf(document)
        except PdfReaderError:
            source.metadata["parse_warning"] = "annex_unreadable"
        else:
            has_text = any(len(re.findall(r"[a-z]", normalize(page.text))) >= 20 for page in pages)
            for page in pages:
                if page.text.strip():
                    evidence.append(Evidence(source_url=url, field="merchant_locations", page=page.number,
                                             text=page.text[:4000], method="pdf-text"))
                for table in page.tables:
                    if len(table) < 2:
                        continue
                    headings = [normalize(str(cell or "")) for cell in table[0]]
                    if not any(re.search(r"comercio|estacion|sucursal|local|nombre", heading) for heading in headings):
                        continue
                    # Keep the whole row: identical brand names do not imply
                    # every branch participates, and city/address are material.
                    for row in table[1:]:
                        value = " | ".join(" ".join(str(cell or "").split()) for cell in row).strip(" |")
                        if value and len(re.findall(r"[a-z]", normalize(value))) >= 3:
                            locations.append(value)
                            evidence.append(Evidence(source_url=url, field="merchant_locations", page=page.number,
                                                     text=value, method="pdf-table"))
            if not locations:
                source.metadata["parse_warning"] = "annex_unrecognized_template" if has_text else "annex_requires_ocr"
    elif document is not None:
        source.metadata["parse_warning"] = "annex_requires_ocr"
    else:
        source.metadata["parse_warning"] = "annex_not_acquired"
    for offer in variants:
        offer.evidence.extend(item.model_copy(deep=True) for item in evidence)
        offer.eligibility.conditions.append("Aplica exclusivamente en los locales del listado oficial: " + url)
        if locations:
            offer.eligibility.locations = list(dict.fromkeys([*offer.eligibility.locations, *locations]))
        elif "merchant_locations" not in offer.eligibility.unknown_fields:
            offer.eligibility.unknown_fields.append("merchant_locations")


def _apply_terms_documents(source, variants, button_url: str, button_label: str) -> None:
    """Preserve linked legal conditions without promoting PDF-wide percentages.

    Legal attachments may describe store discounts, reward points, or multiple
    phases. They are evidence for review; no PDF percentage or table row is
    copied to every card variant, and no legal table becomes a shop list.
    """
    button = official_document_url(button_url, source.source_url, "bancoatlas.com.py") if button_url else None
    expected = button if button and "adherid" not in normalize(button_label) and attachment_mime(button) else None
    documents = [document for document in source.documents
                 if document.metadata.get("role") == "terms" or
                 (not document.metadata.get("role") and expected == document.source_url)]
    if expected and not any(document.source_url == expected for document in documents):
        _unknown_terms(source, variants, expected, "terms_not_acquired")
    for document in documents:
        if document.source_type != "pdf":
            _unknown_terms(source, variants, document.source_url, "terms_requires_ocr")
            continue
        try:
            pages = read_pdf(document)
        except PdfReaderError:
            _unknown_terms(source, variants, document.source_url, "terms_unreadable")
            continue
        if not any(len(re.findall(r"[a-z]", normalize(page.text))) >= 20 for page in pages):
            _unknown_terms(source, variants, document.source_url, "terms_requires_ocr")
            continue
        incomplete_text = any(len(re.findall(r"[a-z]", normalize(page.text))) < 20 for page in pages)
        for offer in variants:
            for page in pages:
                if not page.text.strip():
                    continue
                if page.text not in offer.terms:
                    offer.terms.append(page.text)
                offer.evidence.append(Evidence(source_url=document.source_url, page=page.number,
                                               field="terms", method="pdf-text", text=page.text))
            # A legal table can contain multiple campaigns/phases. Compare
            # explicit validity from the narrative only, never from its rows.
            narrative = "\n".join(page.text for page in pages if not page.tables)
            pdf_start, pdf_end = extract_validity(narrative)
            disagreement = any(pdf is not None and web is not None and pdf != web
                               for pdf, web in [(pdf_start, offer.valid_from), (pdf_end, offer.valid_until)])
            if disagreement:
                offer.validity_state = "conflict"
                if offer.publication != "retired":
                    offer.publication = "pending"
                source.metadata["parse_warning"] = "atlas_html_pdf_conflict"
                offer.evidence.append(Evidence(source_url=document.source_url, field="validity",
                                               method="pdf-text", text=narrative))
        if incomplete_text:
            _unknown_terms(source, variants, document.source_url, "terms_requires_ocr")


def _unknown_terms(source, variants, url: str, warning: str) -> None:
    if source.metadata.get("parse_warning") != "atlas_html_pdf_conflict":
        source.metadata["parse_warning"] = warning
    for offer in variants:
        if offer.publication != "retired":
            offer.publication = "pending"
        if "terms_document" not in offer.eligibility.unknown_fields:
            offer.eligibility.unknown_fields.append("terms_document")
        offer.evidence.append(Evidence(source_url=url, field="terms", method="official-terms-link",
                                       text="Documento legal pendiente de extracción y revisión."))
