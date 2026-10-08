from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.scraping.base import BaseSource
from app.scraping.clients.http_client import HttpClient
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.helpers import SourceDiscoveryError
from app.scraping.sources.bank_documents import attach_bank_documents
from app.scraping.utils.offers import identity

LISTING_URL = "https://www.bancoatlas.com.py/web/beneficios"


def jsonld_offers(soup: BeautifulSoup) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("@type") == "Offer" and value.get("name"):
                result.setdefault(str(value["name"]), []).append(value)
            for child in value.values():
                if isinstance(child, (list, dict)):
                    visit(child)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            visit(json.loads(script.string or ""))
        except (TypeError, ValueError):
            continue
    return result


def extract_atlas_cards(text: str, url: str) -> list[ScrapedSource]:
    """JSON-LD is corroboration, not a substitute for the card's conditions."""
    soup = BeautifulSoup(text, "html.parser")
    structured = jsonld_offers(soup)
    cards = []
    for card in soup.select(".benefit-card[data-nombre]"):
        name = str(card.get("data-nombre", "")).strip()
        if not name:
            continue
        # No numeric source ID is exposed currently. Logo blob is a stable
        # source signal; combine merchant to distinguish reused logos.
        key = str(card.get("data-id") or identity(name, urlparse(str(card.get("data-logo", ""))).path))
        cards.append(ScrapedSource(source_type="html", source_url=url, text=str(card),
                                   metadata={"bank_slug": "atlas", "source_key": "atlas:card:" + key,
                                             "jsonld": json.dumps(structured.get(name, []), ensure_ascii=False)}))
    return cards


class AtlasHtmlSource(BaseSource):
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http_client = http_client or HttpClient()

    def fetch(self) -> list[ScrapedSource]:
        queue, seen, sources, keys = [LISTING_URL], set(), [], set()
        while queue:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            html = self._http_client.get_bytes(url).decode("utf-8", "replace")
            page_sources = extract_atlas_cards(html, url)
            if not page_sources:
                raise SourceDiscoveryError("La página Atlas no contiene cards reconocidas")
            for source in page_sources:
                if source.metadata["source_key"] not in keys:
                    keys.add(source.metadata["source_key"])
                    attach_bank_documents(source, self._http_client, "bancoatlas.com.py", include_images=True)
                    sources.append(source)
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                candidate = urljoin(LISTING_URL, str(a["href"]))
                parsed = urlparse(candidate)
                if parsed.hostname == urlparse(LISTING_URL).hostname and parsed.path == "/web/beneficios" and re.search(r"(?:^|&)page=\d+(?:&|$)", parsed.query):
                    if candidate not in seen and candidate not in queue:
                        queue.append(candidate)
            if len(seen) > 100:
                raise SourceDiscoveryError("La paginación Atlas excedió el límite de 100 páginas")
        return sources
