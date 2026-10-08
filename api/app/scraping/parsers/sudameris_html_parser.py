from __future__ import annotations

import logging
import re
from bs4 import BeautifulSoup

from app.promotions.schemas import Evidence, MerchantLocation
from app.scraping.base import BaseParser
from app.scraping.schemas import ScrapedPromotion, ScrapedSource
from app.scraping.clients.pdf_reader import PdfReaderError
from app.scraping.parsers.pdf_offers import read_pdf, parse_sudameris_tables, parse_sudameris_text, _sudameris_catalogue_rows
from app.scraping.utils.encoding import fix_mojibake
from app.scraping.utils.offers import identity, legacy_summary, make_offer
from app.scraping.utils.schedule import normalize

logger = logging.getLogger(__name__)
_BENEFIT_KEYWORDS = {"reintegro": "cashback", "cashback": "cashback", "cuotas": "installments", "descuento": "discount", "puntos": "points"}


class SudamerisHtmlParser(BaseParser):
    def __init__(self, http_client=None, pdf_reader=None, gemini_client=None) -> None:
        # Accepted for backwards compatibility; acquisition is source-owned.
        self._gemini_client = gemini_client

    def parse(self, source: ScrapedSource) -> list[ScrapedPromotion]:
        soup = BeautifulSoup(source.text or "", "html.parser")
        title = _extract_title(soup) or fix_mojibake(source.metadata.get("link_text", ""))
        if not title:
            return []
        content = soup.find("div", class_="description-promo")
        text = fix_mojibake(content.get_text("\n", strip=True)) if content else ""
        # Purchase schedule and validity live in the same dedicated promo body.
        offers = []
        complete = bool(source.documents)
        raw_text = text
        for document in source.documents:
            try:
                pages = read_pdf(document)
            except PdfReaderError:
                complete = False
                source.metadata["parse_warning"] = "pdf_unreadable"
                continue
            pdf_text = "\n".join(page.text for page in pages)
            if not pdf_text.strip():
                complete = False
                source.metadata["parse_warning"] = "pdf_requires_ocr"
                continue
            raw_text += "\n" + pdf_text
            rows = parse_sudameris_tables(pages, document.source_url)
            table_pages = [page for page in pages if page.tables]
            annexes = [(page, table, _adherent_rows(table)) for page in table_pages for table in page.tables]
            if rows and any(locations is None and not _sudameris_catalogue_rows(table) for _, table, locations in annexes):
                complete = False
                source.metadata["parse_warning"] = "unrecognized_offer_variants"
            if any("offer_variant" in offer.eligibility.unknown_fields for offer in rows):
                complete = False
                source.metadata["parse_warning"] = "duplicate_merchant_variants"
            if not rows and (not annexes or all(locations is not None for _, _, locations in annexes)):
                # A recognized address annex is not a benefits catalogue. Read
                # the one-merchant narrative separately, then bind only its
                # explicitly named physical/app locations to each variant.
                narrative = "\n".join(page.text for page in pages if not page.tables)
                rows = parse_sudameris_text(narrative, document.source_url, title)
                _apply_adherents(rows, annexes, document.source_url)
            if not rows:
                complete = False
            if rows:
                for offer in rows:
                    # Text templates previously dropped EXCLUSIONES. Preserve
                    # the complete official pages, including payment restrictions.
                    offer.terms.extend(page.text for page in pages if not page.tables and page.text and page.text not in offer.terms)
                    for evidence in [*offer.evidence, *offer.schedule.evidence]:
                        if evidence.page is None:
                            needle = " ".join(normalize(evidence.text).split())
                            matches = [page for page in pages if needle and needle in " ".join(normalize(page.text).split())]
                            if len(matches) == 1:
                                evidence.page = matches[0].number
                                evidence.method = "pdf-text"
                    for page in pages:
                        if not page.tables and re.search(r"condiciones|exclusiones", normalize(page.text)):
                            offer.evidence.append(Evidence(source_url=document.source_url, page=page.number,
                                                           field="eligibility", method="pdf-text", text=page.text[:4000]))
                _compare_html_and_pdf(rows, text, title, source)
                offers.extend(rows)
            elif not offers and pdf_text:
                complete = False
                offer = make_offer(merchant=title, variant="general", source_url=document.source_url,
                                   text=pdf_text, default_type=_extract_benefit_type(title + " " + pdf_text))
                if len({b.percentage for b in offer.benefits if b.percentage is not None}) > 1 or any(p.tables for p in pages):
                    offer.publication = "pending"
                    source.metadata["parse_warning"] = "unrecognized_offer_variants"
                offers.append(offer)
        if not offers:
            offers = parse_sudameris_text(text, source.source_url, title)
            complete = bool(offers) and not source.documents
            if not offers:
                offers = [make_offer(merchant=title, variant="general", source_url=source.source_url,
                                     text=text, default_type=_extract_benefit_type(title + " " + text))]
        if source.metadata.get("document_error") or source.metadata.get("parse_warning") in {"pdf_unreadable", "pdf_requires_ocr"}:
            # A short web summary cannot confirm missing caps/exclusions from
            # a terms PDF that was linked but could not be read.
            for offer in offers:
                offer.publication = "pending"
                if "terms_document" not in offer.eligibility.unknown_fields:
                    offer.eligibility.unknown_fields.append("terms_document")
        grouped = {}
        for offer in offers:
            grouped.setdefault(normalize(offer.merchant_name), []).append(offer)
        source_key = "sudameris:" + (source.metadata.get("promo_id") or "url:" + identity(source.source_url))
        metadata = {**source.metadata, "source_url": source.source_url}
        metadata["offers_complete"] = "true" if complete and not source.metadata.get("document_error") and not source.metadata.get("parse_warning") else "false"
        categories = []
        for tag in soup.find_all("a", href=True):
            if "/categoria/" in str(tag["href"]):
                categories.append(tag.get_text(" ", strip=True))
        results = []
        for variants in grouped.values():
            merchant = variants[0].merchant_name
            kind, percentage = legacy_summary(variants)
            starts, ends = {o.valid_from for o in variants}, {o.valid_until for o in variants}
            results.append(ScrapedPromotion(bank_slug="sudameris", title=merchant,
                           merchant_name=merchant, campaign_name=title, description=text or None,
                           category_name=categories[0] if categories else None,
                           benefit_type=kind, discount_percentage=percentage,
                           start_date=next(iter(starts)) if len(starts) == 1 else None,
                           end_date=next(iter(ends)) if len(ends) == 1 else None,
                           raw_text=raw_text, source_key=source_key + ":merchant:" + identity(merchant),
                           offers=variants, metadata=metadata))
        return results

# --- helpers de extracción por regex ---

def _extract_title(soup: BeautifulSoup) -> str | None:
    for tag in ("h3", "h4", "h2", "h1"):
        el = soup.find(tag)
        if el:
            text = fix_mojibake(el.get_text(" ", strip=True))
            if text and text.lower() not in {"promociones relacionadas", "buscador de comercios"}:
                return text
    return None


def _extract_benefit_type(text: str) -> str | None:
    lower = text.lower()
    for keyword, benefit_type in _BENEFIT_KEYWORDS.items():
        if keyword in lower:
            return benefit_type
    return None


def _adherent_rows(table) -> list[list[str]] | None:
    if not table:
        return None
    headers = [normalize(" ".join(str(cell or "").split())) for cell in table[0]]
    labels = [value for value in headers if value]
    if len(labels) != 3 or not re.search(r"comercio|estacion", labels[0]) or "direccion" not in labels[1] or "localidad" not in labels[2]:
        return None
    width = len(headers)
    if width == 3:
        columns, spacers = [0, 1, 2], []
    elif width == 9 and all(headers[index] == "" for index in range(9) if index % 3 != 1):
        columns, spacers = [0, 3, 6], [index for index in range(9) if index % 3]
    else:
        return None
    rows = []
    for row in table[1:]:
        if len(row) != width or any(str(row[index] or "").strip() for index in spacers):
            return None
        cells = [" ".join(str(row[index] or "").split()) for index in columns]
        if all(cells):
            rows.append(cells)
        elif any(cells):
            return None
    return rows or None


def _apply_adherents(offers, annexes, url: str) -> None:
    app_annexes = {page.number: bool(re.search(r"estaciones\s+(?:de\s+servicios?\s*[-–]?\s*)?(?:mienex|mi\s+app\s+enex|app)", normalize(page.text)))
                  for page, _, _ in annexes}
    has_app_split = len(set(app_annexes.values())) > 1
    for offer in offers:
        app_offer = any(re.search(r"a\s+traves\s+de\s+la\s+app", normalize(benefit.conditions[0]))
                        for benefit in offer.benefits if benefit.conditions)
        selected = [(page, locations) for page, _, locations in annexes
                    if not has_app_split or app_annexes[page.number] == app_offer]
        for page, locations in selected:
            for cells in locations or []:
                value = " | ".join(cells)
                offer.eligibility.locations.append(value)
                offer.eligibility.cities.append(cells[2])
                evidence = Evidence(source_url=url, page=page.number, field="merchant_locations",
                                    method="pdf-table", text=value)
                offer.evidence.append(evidence)
                offer.locations.append(MerchantLocation(key=identity(*cells), name=cells[0], address=cells[1], city=cells[2],
                                                        channels=offer.eligibility.channels.copy(), processors=offer.eligibility.processors.copy(),
                                                        evidence=[evidence.model_copy(deep=True)]))
        offer.eligibility.locations = list(dict.fromkeys(offer.eligibility.locations))
        offer.eligibility.cities = list(dict.fromkeys(offer.eligibility.cities))
        if selected:
            offer.locations = list({location.key: location for location in offer.locations}.values())
            offer.location_scope = "specified"
            offer.eligibility.conditions.append("Aplica exclusivamente en las estaciones del listado oficial correspondiente a esta modalidad.")


def _compare_html_and_pdf(offers, html_text: str, title: str, source: ScrapedSource) -> None:
    """Keep disagreements visible when both sources describe one merchant.

    A campaign summary must not override the individual rows in a catalogue
    PDF; only compare a matching merchant's full one-merchant template.
    """
    if not html_text or not offers or any(normalize(offer.merchant_name) != normalize(title) for offer in offers):
        return
    summary = make_offer(merchant=title, variant="html-summary", source_url=source.source_url,
                         text=html_text, default_type=_extract_benefit_type(title + " " + html_text))
    for offer in offers:
        validity_conflict = any(web is not None and pdf is not None and web != pdf
                                for web, pdf in [(summary.valid_from, offer.valid_from), (summary.valid_until, offer.valid_until)])
        schedule_conflict = summary.schedule.state == offer.schedule.state == "known" and summary.schedule.model_dump(exclude={"evidence"}) != offer.schedule.model_dump(exclude={"evidence"})
        if validity_conflict:
            offer.validity_state = "conflict"
        if schedule_conflict:
            offer.schedule.state = "conflict"
        if validity_conflict or schedule_conflict:
            source.metadata["parse_warning"] = "html_pdf_conflict"
            offer.publication = "pending"
            offer.evidence.append(Evidence(source_url=source.source_url, field="validity" if validity_conflict else "schedule",
                                           text=html_text, method="html-summary"))
