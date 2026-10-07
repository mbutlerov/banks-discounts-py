from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.scraping.clients.http_client import HttpClient, HttpClientError
from app.scraping.schemas import ScrapedSource

logger = logging.getLogger(__name__)


class SourceDiscoveryError(RuntimeError):
    """An unavailable/changed catalogue is not an empty successful scrape."""


def canonical_url(url: str, base: str) -> str:
    parsed = urlparse(urljoin(base, url))
    return urlunparse(parsed._replace(fragment=""))


def attach_pdfs(source: ScrapedSource, client: HttpClient) -> None:
    soup = BeautifulSoup(source.text or "", "html.parser")
    urls = list(dict.fromkeys(canonical_url(str(a["href"]), source.source_url)
                             for a in soup.find_all("a", href=True)
                             if urlparse(str(a["href"])).path.lower().endswith(".pdf")))
    allowed_host = urlparse(source.source_url).hostname
    for url in urls:
        if urlparse(url).hostname != allowed_host:
            continue
        if "pdf_url" not in source.metadata:
            source.metadata["pdf_url"] = url
        try:
            source.documents.append(ScrapedSource(source_type="pdf", source_url=url,
                                                  content=client.get_bytes(url), mime_type="application/pdf"))
        except HttpClientError:
            source.metadata["document_error"] = "download_failed"
            logger.warning("No se pudo adquirir un PDF de %s", source.source_url)
