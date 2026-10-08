from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.scraping.base import BaseSource
from app.scraping.clients.http_client import HttpClient, HttpClientError
from app.scraping.schemas import ScrapedSource
from app.scraping.utils.encoding import fix_mojibake
from app.scraping.sources.helpers import SourceDiscoveryError
from app.scraping.sources.bank_documents import attach_bank_documents, attachment_mime, official_document_url

logger = logging.getLogger(__name__)

_LISTING_URL = "https://www.sudameris.com.py/beneficios/"
_BASE_URL = "https://www.sudameris.com.py"
_PROMO_ID_RE = re.compile(r"/(destacado|promocion)/(\d+)/")


class SudamerisHtmlSource(BaseSource):
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._http_client = http_client or HttpClient()

    def fetch(self) -> list[ScrapedSource]:
        # Both catalogue routes matter. Their numeric IDs have separate namespaces.
        detail_urls: dict[str, tuple[str, str]] = {}
        for listing in [_LISTING_URL, urljoin(_LISTING_URL, "promociones")]:
            raw = self._http_client.get_bytes(listing)
            soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
            for a in soup.find_all("a", href=True):
                href = str(a["href"])
                m = _PROMO_ID_RE.search(href)
                if not m or "/detalle" not in href:
                    continue
                full_url = urljoin(_BASE_URL, href)
                if full_url not in detail_urls:
                    detail_urls[full_url] = (fix_mojibake(a.get_text(" ", strip=True)), f"{m[1]}:{m[2]}")
        if not detail_urls:
            raise SourceDiscoveryError("El catálogo Sudameris no contiene detalles reconocidos")

        logger.info("Sudameris: %d promos encontradas en listado.", len(detail_urls))

        sources: list[ScrapedSource] = []
        failed_details = 0
        for url, (link_text, promo_id) in detail_urls.items():
            try:
                page_raw = self._http_client.get_bytes(url)
                page_html = page_raw.decode("utf-8", errors="replace")
                pdf_url = _find_pdf_url(page_html, url)
                meta = {"link_text": link_text, "promo_id": promo_id}
                if pdf_url:
                    meta["pdf_url"] = pdf_url
                source = ScrapedSource(
                    source_type="html",
                    source_url=url,
                    text=page_html,
                    metadata=meta,
                )
                attach_bank_documents(source, self._http_client, "sudameris.com.py")
                sources.append(source)
            except HttpClientError as exc:
                failed_details += 1
                logger.warning("No se pudo obtener %s: %s", url, exc)

        if not sources:
            raise SourceDiscoveryError(f"No se pudieron descargar los {len(detail_urls)} detalles descubiertos de Sudameris")
        if failed_details:
            for source in sources:
                source.metadata["discovery_warning"] = f"detail_download_failed:{failed_details}/{len(detail_urls)}"
        return sources


def _find_pdf_url(html: str, base_url: str = _BASE_URL) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        url = official_document_url(str(a["href"]), base_url, "sudameris.com.py")
        if url and attachment_mime(url) == "application/pdf":
            return url
    return None
