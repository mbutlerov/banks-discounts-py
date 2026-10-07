"""Historical PDF source, selected explicitly as ueno-pdf."""
from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.scraping.base import BaseSource
from app.scraping.clients.http_client import HttpClient
from app.scraping.schemas import ScrapedSource

logger = logging.getLogger(__name__)


class UenoSourceError(Exception):
    """Error específico para la obtención de la fuente de ueno."""


class UenoSource(BaseSource):
    LANDING_URL = "https://www.ueno.com.py/alianzas-ueno/"

    def __init__(self, http_client: HttpClient) -> None:
        self._http_client = http_client

    def fetch(self) -> list[ScrapedSource]:
        pdf_url = self.fetch_pdf_url()
        logger.info("PDF de Ueno encontrado: %s", pdf_url)
        pdf_content = self._http_client.get_bytes(pdf_url)

        return [ScrapedSource(
            source_type="pdf",
            source_url=pdf_url,
            content=pdf_content,
            text=None,
            file_name=pdf_url.split("/")[-1],
            mime_type="application/pdf",
            metadata={
                "bank_slug": "ueno",
                "landing_url": self.LANDING_URL,
            },
        )]

    def fetch_pdf_url(self) -> str:
        html = self._http_client.get_text(self.LANDING_URL)
        soup = BeautifulSoup(html, "html.parser")

        pdf_url = self._extract_pdf_url_from_iframe(soup)
        if pdf_url:
            return pdf_url

        pdf_url = self._extract_pdf_url_from_download_link(soup)
        if pdf_url:
            return pdf_url

        raise UenoSourceError(
            "No se pudo encontrar el enlace del PDF de promociones de ueno."
        )

    def _extract_pdf_url_from_iframe(self, soup: BeautifulSoup) -> str | None:
        iframe = soup.find("iframe", src=True)
        if not iframe:
            return None

        src = iframe["src"].strip()
        if not src.lower().endswith(".pdf"):
            return None

        return urljoin(self.LANDING_URL, src)

    def _extract_pdf_url_from_download_link(self, soup: BeautifulSoup) -> str | None:
        for link in soup.find_all("a", href=True):
            href = link["href"].strip()
            text = link.get_text(" ", strip=True).lower()

            if href.lower().endswith(".pdf"):
                return urljoin(self.LANDING_URL, href)

            if "descargar pdf" in text:
                return urljoin(self.LANDING_URL, href)

        return None
