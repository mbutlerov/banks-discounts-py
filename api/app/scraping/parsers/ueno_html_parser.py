from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from app.scraping.base import BaseParser
from app.scraping.clients.http_client import HttpClient
from app.scraping.clients.pdf_reader import PdfReader, PdfReaderError
from app.scraping.schemas import ScrapedPromotion, ScrapedSource
from app.scraping.parsers.pdf_offers import read_pdf, parse_ueno_tables
from app.scraping.parsers.ueno_legal_tables import incomplete_legal_table, parse_ueno_legal_tables
from app.scraping.utils.offers import identity, make_offer, legacy_summary
from app.scraping.utils.encoding import fix_mojibake

logger = logging.getLogger(__name__)

_BENEFIT_KEYWORDS: dict[str, str] = {
    "reintegro": "cashback",
    "cashback": "cashback",
    "cuotas": "installments",
    "descuento": "discount",
    "puntos": "points",
}

_BENEFIT_WORDS_RE = r"(?:reintegro|cuotas?\s+sin\s+intereses|descuento|cashback|puntos|canje\s+de\s+upys(?:\s+x)?|entradas|bono(?:\s+de\s+bienvenida)?)"

_TITLE_PREFIXES = (
    rf"beneficios?\s+de\s+{_BENEFIT_WORDS_RE}\s+",
    rf"beneficios?\s+{_BENEFIT_WORDS_RE}\s+",
    rf"promoci[oó]n\s+{_BENEFIT_WORDS_RE}\s+",
    r"beneficios?\s+de\s+",
    r"beneficios?\s+",
    r"promoci[oó]n\s+",
    r"cuotas?\s+sin\s+intereses\s+",
)

_DATE_SUFFIX_RE = re.compile(r"\s*\|\s*(?:[A-Z]{3}\s*)?\d{4}\w*\s*$", re.IGNORECASE)


class UenoHtmlParser(BaseParser):
    def __init__(
        self,
        http_client: HttpClient | None = None,
        pdf_reader: PdfReader | None = None,
    ) -> None:
        self._http_client = http_client
        self._pdf_reader = pdf_reader

    def parse(self, source: ScrapedSource) -> list[ScrapedPromotion]:
        soup = BeautifulSoup(source.text or "", "html.parser")
        title = self._extract_title(source, soup)
        if not title:
            return []
        merchant = _extract_merchant_name(title) or title
        metadata = {**source.metadata, "source_url": source.source_url, "source_type": source.source_type}
        source_key = "ueno:url:" + identity(source.source_url)
        offers = []
        complete = bool(source.documents)
        raw_texts = []
        for document in source.documents:
            try:
                pages = read_pdf(document)
            except PdfReaderError:
                complete = False
                source.metadata["parse_warning"] = "pdf_unreadable"
                continue
            text = "\n".join(page.text for page in pages)
            if not text.strip():
                complete = False
                source.metadata["parse_warning"] = "pdf_requires_ocr"
                continue
            raw_texts.append(text)
            table_offers = parse_ueno_tables(pages, document.source_url)
            if not table_offers:
                table_offers = parse_ueno_legal_tables(pages, document.source_url, merchant)
                if incomplete_legal_table(pages):
                    complete = False
                    source.metadata["parse_warning"] = "ueno_incomplete_legal_table"
                    for offer in table_offers:
                        offer.publication = "pending"
            if table_offers:
                offers.extend(table_offers)
                continue
            offer = make_offer(merchant=merchant, variant="general", source_url=document.source_url,
                               text=text, default_type=_extract_benefit_type(title))
            complete = False
            # A single percentage in an unrecognized legal schema still does
            # not prove which merchants/cards/caps it belongs to. Only the
            # recognized document interpreters may publish automatically.
            offer.publication = "pending"
            # Different numbers without a recognized table must never become
            # concurrent universal benefits for a generic merchant.
            percentages = {benefit.percentage for benefit in offer.benefits if benefit.percentage is not None}
            if len(percentages) > 1 or any(len(t) > 2 for p in pages for t in p.tables):
                offer.publication = "pending"
                source.metadata["parse_warning"] = "unrecognized_offer_variants"
            offers.append(offer)
        if not offers:
            complete = False
            # The URL month is a hint, never proof of contractual validity.
            offer = make_offer(merchant=merchant, variant="general", source_url=source.source_url,
                               text=title, default_type=_extract_benefit_type(title))
            offer.publication = "pending"
            offers = [offer]
        grouped = {}
        for offer in offers:
            grouped.setdefault(offer.merchant_name, []).append(offer)
        results = []
        for name, variants in grouped.items():
            kind, percentage = legacy_summary(variants)
            starts = {o.valid_from for o in variants}
            ends = {o.valid_until for o in variants}
            raw_text = "\n".join(raw_texts)
            result_meta = dict(metadata)
            result_meta.update({k: v for k, v in source.metadata.items() if k in {"parse_warning", "document_error"}})
            result_meta["offers_complete"] = "true" if complete and not source.metadata.get("document_error") and not source.metadata.get("parse_warning") else "false"
            results.append(ScrapedPromotion(bank_slug="ueno", title=name,
                           merchant_name=name, campaign_name=title, description=title, category_name=_category_name(title),
                           benefit_type=kind, discount_percentage=percentage,
                           start_date=next(iter(starts)) if len(starts) == 1 else None,
                           end_date=next(iter(ends)) if len(ends) == 1 else None,
                           raw_text=raw_text or title, metadata=result_meta,
                           source_key=source_key + ":merchant:" + identity(name), offers=variants))
        return results

    def _extract_title(self, source: ScrapedSource, soup: BeautifulSoup | None) -> str | None:
        if soup:
            for tag in ("h6", "h5", "h4", "h3", "h2", "h1"):
                el = soup.find(tag)
                if el:
                    text = fix_mojibake(el.get_text(" ", strip=True))
                    if text:
                        return text

        link_text = source.metadata.get("link_text")
        return fix_mojibake(link_text) if link_text else None


def _extract_benefit_type(title: str) -> str | None:
    title_lower = title.lower()
    for keyword, benefit_type in _BENEFIT_KEYWORDS.items():
        if keyword in title_lower:
            return benefit_type
    return None


def _category_name(title: str) -> str | None:
    value = fix_mojibake(title).lower()
    for keywords, category in [(("supermercad",), "Supermercados"),
                               (("combust", "petropar"), "Combustibles"),
                               (("farmaci", "bienestar", "óptica"), "Salud & Bienestar"),
                               (("la cuadrita", "gastronom"), "Gastronomía"),
                               (("entretenimiento", "clubes", "deportes"), "Entretenimiento")]:
        if any(word in value for word in keywords):
            return category
    return None


def _extract_merchant_name(title: str) -> str | None:
    cleaned = _DATE_SUFFIX_RE.sub("", title).strip()

    for prefix_pattern in _TITLE_PREFIXES:
        before = cleaned
        cleaned = re.sub(f"^{prefix_pattern}", "", cleaned, flags=re.IGNORECASE).strip()
        if cleaned != before:
            break

    return cleaned or None
