"""Read Ueno's flyer as navigation; legal documents provide offer conditions.

Logos and overlapping flyer text cannot reliably identify merchant variants.
PDF link annotations reach the legal pages without OCR or paid AI calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from urllib.parse import urljoin, urlparse, urlunparse

import pdfplumber
from bs4 import BeautifulSoup

from app.scraping.sources.helpers import SourceDiscoveryError

ALLIANCES_URL = "https://www.ueno.com.py/alianzas-ueno/"
UENO_HOSTS = {"www.ueno.com.py", "ueno.com.py"}


def official_ueno_url(value: str, base: str) -> str | None:
    parsed = urlparse(urljoin(base, value.strip()))
    try:
        allowed_port = parsed.port in {None, 80, 443}
    except ValueError:
        return None
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in UENO_HOSTS
            or parsed.username or parsed.password or not allowed_port):
        return None
    return urlunparse(parsed._replace(scheme="https", netloc="www.ueno.com.py", fragment=""))


def catalogue_pdf_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    # The first iframe commonly belongs to Google Tag Manager.
    for tag, attribute in (("iframe", "src"), ("embed", "src"), ("object", "data"), ("a", "href")):
        for node in soup.find_all(tag, attrs={attribute: True}):
            value = official_ueno_url(str(node[attribute]), ALLIANCES_URL)
            if value and urlparse(value).path.lower().endswith(".pdf") and value not in candidates:
                candidates.append(value)
    return candidates


@dataclass
class CatalogueDetail:
    url: str
    pages: list[int] = field(default_factory=list)


def catalogue_details(content: bytes, catalogue_url: str) -> tuple[list[CatalogueDetail], int]:
    details: dict[str, CatalogueDetail] = {}
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            for number, page in enumerate(pdf.pages, 1):
                for link in page.hyperlinks:
                    url = official_ueno_url(str(link.get("uri") or ""), catalogue_url)
                    if not url or not urlparse(url).path.startswith("/beneficio-byc/"):
                        continue
                    detail = details.setdefault(url, CatalogueDetail(url))
                    if number not in detail.pages:
                        detail.pages.append(number)
    except Exception as exc:
        raise SourceDiscoveryError("No se pudo leer el PDF del catálogo mensual Ueno") from exc
    return list(details.values()), page_count
