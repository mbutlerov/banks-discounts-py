from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.promotions.schemas import Evidence
from app.scraping.base import BaseParser
from app.scraping.clients.pdf_reader import PdfReaderError
from app.scraping.parsers.pdf_offers import PdfPage, read_pdf
from app.scraping.schemas import ScrapedPromotion, ScrapedSource
from app.scraping.utils.offers import extract_caps, extract_validity, identity, legacy_summary, make_offer


def _gnb_pdf_sections(text: str) -> tuple[str, str] | None:
    """Return validity and benefit blocks for the two observed GNB templates."""
    heading = re.compile(
        r"(?im)^\s*([1-4])\s*\.\s*(Vigencia|Condiciones|Beneficio|Mec[aá\uFFFD]nica)\b"
    )
    expected = {
        (("1", "vigencia"), ("2", "condiciones"), ("3", "beneficio"), ("4", "mecánica")),
        (("1", "vigencia"), ("1", "condiciones"), ("2", "beneficio"), ("3", "mecánica")),
    }
    matches = list(heading.finditer(text))
    for index in range(len(matches) - 3):
        sequence = matches[index:index + 4]
        actual = tuple((number, "mecánica" if label.lower().startswith("mec") else label.lower())
                       for number, label in (match.groups() for match in sequence))
        if actual in expected:
            return (text[sequence[0].end():sequence[1].start()],
                    text[sequence[2].end():sequence[3].start()])
    return None


def parse_gnb_pdf(pages: list[PdfPage], url: str, merchant: str) -> list:
    """Parse bounded single-merchant GNB terms, preserving credit/prepaid tiers."""
    text = "\n".join(page.text for page in pages)
    sections = _gnb_pdf_sections(text)
    if not sections:
        return []
    validity_text, benefits_text = sections
    start, end = extract_validity(validity_text)
    schedule = re.search(r"El beneficio aplica.*?(?:\.|\n\s*[•●])", text, re.I | re.S)
    schedule_text = " ".join(schedule[0].split()) if schedule else ""
    clauses = [clause.strip() for clause in re.split(r"(?m)[•●]|^\s*�\s+", benefits_text) if "%" in clause]
    offers = []
    for i, clause in enumerate(clauses):
        card_condition = re.sub(r"^.*?para\s+pagos\s+con\s+", "", clause, flags=re.I)
        offer = make_offer(merchant=merchant, variant="gnb:" + card_condition, source_url=url,
                           text=text, days=schedule_text, benefits_text=clause, card_text=card_condition,
                           valid_from=start, valid_until=end,
                           evidence=[Evidence(source_url=url, page=pages[0].number, field="benefit", text=clause),
                                     Evidence(source_url=url, field="validity", text=validity_text)])
        if "prepag" in card_condition.lower():
            # The cited credit-account cap is not established for prepaid cards.
            offer.caps = []
            offer.eligibility.unknown_fields.append("caps")
        else:
            offer.caps = extract_caps(text)
        offer.eligibility.processors = ["Bancard"] if "bancard" in text.lower() else []
        offer.eligibility.conditions.append("Consultar sucursales y canales directos en el PDF oficial")
        offers.append(offer)
    return offers


class GnbHtmlParser(BaseParser):
    def parse(self, source: ScrapedSource) -> list[ScrapedPromotion]:
        if source.source_type == "json" and source.metadata.get("source_type") == "gnb_api":
            from app.scraping.parsers.gnb_api_parser import parse_gnb_json
            return parse_gnb_json(source, parse_gnb_pdf)
        soup = BeautifulSoup(source.text or "", "html.parser")
        # A single detail, not the whole listing: navigation/related cards must
        # never contribute benefits to this merchant.
        body = soup.select_one("main, .beneficio-detalle, .benefit-detail")
        heading = (body or soup).find(["h1", "h2", "h4"])
        merchant = source.metadata.get("merchant_name") or (heading.get_text(" ", strip=True) if heading else source.metadata.get("link_text", ""))
        if not merchant and source.source_type == "pdf":
            merchant = source.metadata.get("link_text", "")
        if not merchant:
            return []
        offers, raw_text = [], ""
        documents = [source] if source.source_type == "pdf" else source.documents
        complete = bool(documents)
        for document in documents:
            try:
                pages = read_pdf(document)
            except PdfReaderError:
                complete = False
                source.metadata["parse_warning"] = "pdf_unreadable"
                continue
            raw_text += "\n" + "\n".join(page.text for page in pages)
            parsed = parse_gnb_pdf(pages, document.source_url, merchant)
            if not parsed:
                complete = False
                source.metadata["parse_warning"] = "gnb_pdf_template_unrecognized"
            offers.extend(parsed)
        if not offers:
            raw_text = body.get_text("\n", strip=True) if body else ""
            offer = make_offer(merchant=merchant, variant="unverified", source_url=source.source_url,
                               text=raw_text)
            # Current v2 markup has not been verified because the portal denies
            # acquisition. Keep HTML-only extraction pending until a fixture
            # proves the exact merchant/conditions association.
            offer.publication = "pending"
            offers = [offer]
            source.metadata["parse_warning"] = "gnb_html_requires_verified_fixture"
        kind, percentage = legacy_summary(offers)
        source.metadata["offers_complete"] = "true" if complete and not source.metadata.get("parse_warning") and not source.metadata.get("document_error") else "false"
        return [ScrapedPromotion(bank_slug="gnb", title=merchant, merchant_name=merchant,
                                 description=raw_text[:2000], raw_text=raw_text,
                                 source_key="gnb:" + (source.metadata.get("promo_id") or "url:" + identity(source.source_url)),
                                 benefit_type=kind, discount_percentage=percentage,
                                 start_date=offers[0].valid_from, end_date=offers[0].valid_until,
                                 offers=offers, metadata={**source.metadata, "source_url": source.source_url})]
