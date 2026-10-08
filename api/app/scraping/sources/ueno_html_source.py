from __future__ import annotations

import hashlib
import logging
import re

from bs4 import BeautifulSoup

from app.scraping.base import BaseSource
from app.scraping.clients.http_client import HttpClient, HttpClientError
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.helpers import SourceDiscoveryError, attach_pdfs
from app.scraping.sources.ueno_catalog import (
    ALLIANCES_URL, catalogue_details, catalogue_pdf_urls, official_ueno_url,
)

logger = logging.getLogger(__name__)
BASE_URL = "https://www.ueno.com.py"
LEGAL_URL = f"{BASE_URL}/informacion-legal/"
_URL_MONTH_YEAR_RE = re.compile(r"/beneficio-byc/([a-z]{3})(\d{4})/", re.IGNORECASE)
_MONTH_SLUG_MAP = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
                   "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


class UenoHtmlSourceError(Exception):
    pass


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


class UenoHtmlSource(BaseSource):
    def __init__(self, http_client: HttpClient, max_details: int = 120) -> None:
        self._http_client = http_client
        self._max_details = max_details

    def fetch(self) -> list[ScrapedSource]:
        links: dict[str, dict[str, str]] = {}
        warnings: list[str] = []
        try:
            html = _decode(self._http_client.get_bytes(ALLIANCES_URL))
            pdf_urls = catalogue_pdf_urls(html)
            if not pdf_urls:
                raise SourceDiscoveryError("Alianzas Ueno no contiene un PDF mensual reconocible")
            for pdf_url in pdf_urls:
                content = self._http_client.get_bytes(pdf_url)
                details, page_count = catalogue_details(content, pdf_url)
                if not details:
                    warnings.append("El PDF mensual no contiene enlaces legales reconocibles; requiere revision")
                content_hash = hashlib.sha256(content).hexdigest()
                for detail in details:
                    links.setdefault(detail.url, {
                        "landing_url": ALLIANCES_URL, "catalogue_role": "monthly-primary",
                        "catalogue_pdf_url": pdf_url, "catalogue_pdf_hash": content_hash,
                        "catalogue_pages": ",".join(map(str, detail.pages)),
                        "catalogue_page_count": str(page_count),
                    })
        except (HttpClientError, SourceDiscoveryError):
            warnings.append("No se pudo recorrer el catalogo mensual de Alianzas Ueno; se uso el indice legal complementario")
            logger.warning("No se pudo descubrir el catalogo mensual Ueno")

        primary_count = len(links)
        try:
            legal_links = self._fetch_benefit_links()
            def priority(item):
                match = _URL_MONTH_YEAR_RE.search(item[0])
                # URL periods order discovery only; never contractual validity.
                return (int(match[2]), _MONTH_SLUG_MAP.get(match[1].lower(), 0)) if match else (9999, 0)
            legal_links.sort(key=priority, reverse=True)
            for url, link_text in legal_links:
                if url in links:
                    links[url]["also_in_legal_index"] = "true"
                    if link_text:
                        links[url]["link_text"] = link_text
                else:
                    links[url] = {"landing_url": LEGAL_URL, "catalogue_role": "legal-complement", "link_text": link_text}
        except (HttpClientError, SourceDiscoveryError):
            warnings.append("No se pudo recorrer el indice legal complementario Ueno")
            logger.warning("No se pudo descubrir el indice legal Ueno")
        if not links:
            raise SourceDiscoveryError("No se encontraron beneficios Ueno en Alianzas ni en el indice legal")

        discovered = len(links)
        if discovered > self._max_details:
            warnings.append(f"Catalogo limitado a {self._max_details} de {discovered} detalles; recorrido parcial, sin retiros por ausencia")
        sources: list[ScrapedSource] = []
        failures = 0
        for url, provenance in list(links.items())[:self._max_details]:
            try:
                source = ScrapedSource(source_type="html", source_url=url,
                                       text=_decode(self._http_client.get_bytes(url)),
                                       metadata={"bank_slug": "ueno", **provenance,
                                                 "primary_detail_count": str(primary_count)})
                attach_pdfs(source, self._http_client)
                sources.append(source)
            except HttpClientError:
                failures += 1
                logger.warning("No se pudo obtener un detalle Ueno: %s", url)
        if not sources:
            raise SourceDiscoveryError("Los beneficios Ueno descubiertos no pudieron descargarse")
        if failures:
            warnings.append(f"No se pudieron descargar {failures} detalles Ueno; recorrido parcial")
        if warnings:
            sources[0].metadata["discovery_warning"] = " | ".join(warnings)
        return sources

    def _fetch_benefit_links(self) -> list[tuple[str, str]]:
        soup = BeautifulSoup(_decode(self._http_client.get_bytes(LEGAL_URL)), "html.parser")
        seen: set[str] = set()
        links: list[tuple[str, str]] = []
        # Collapsed accordions/tabs are present in server-rendered HTML.
        for node in soup.find_all("a", href=True):
            url = official_ueno_url(str(node["href"]), LEGAL_URL)
            if not url or "/beneficio-byc/" not in url or url in seen:
                continue
            seen.add(url)
            links.append((url, node.get_text(" ", strip=True)))
        if not links:
            raise SourceDiscoveryError("El indice legal Ueno no contiene beneficios reconocibles")
        return links
