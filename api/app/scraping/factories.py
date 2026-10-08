from __future__ import annotations

from typing import TYPE_CHECKING

from app.scraping.registry import BANKS

if TYPE_CHECKING:
    from app.scraping.base import BaseParser, BaseSource
    from app.scraping.clients.http_client import HttpClient


def _experimental_settings():
    from app.core.config import settings

    if not settings.ENABLE_AI_ENRICHMENT:
        raise ValueError("El adaptador experimental ueno-pdf requiere ENABLE_AI_ENRICHMENT=true")
    return settings


def get_source(bank_slug: str, http_client: HttpClient | None = None) -> BaseSource:
    if bank_slug not in (*BANKS, "ueno-pdf"):
        raise ValueError(f"No existe un source configurado para el banco: {bank_slug}")
    if bank_slug == "ueno-pdf":
        _experimental_settings()
    # GNB owns its client so it can opt into the verified HTTPX transport.
    if http_client is None and bank_slug != "gnb":
        from app.scraping.clients.http_client import HttpClient

        http_client = HttpClient()

    match bank_slug:
        case "ueno":
            from app.core.config import settings
            from app.scraping.sources.ueno_html_source import UenoHtmlSource

            return UenoHtmlSource(http_client=http_client, max_details=settings.SCRAPING_MAX_DETAILS_PER_BANK)
        case "ueno-pdf":
            from app.scraping.experimental.ueno_source import UenoSource

            return UenoSource(http_client=http_client)
        case "sudameris":
            from app.scraping.sources.sudameris_html_source import SudamerisHtmlSource

            return SudamerisHtmlSource(http_client=http_client)
        case "itau":
            from app.core.config import settings
            from app.scraping.sources.itau_html_source import ItauHtmlSource

            return ItauHtmlSource(http_client=http_client, max_details=settings.SCRAPING_MAX_DETAILS_PER_BANK)
        case "atlas":
            from app.scraping.sources.atlas_html_source import AtlasHtmlSource

            return AtlasHtmlSource(http_client=http_client)
        case "gnb":
            from app.scraping.sources.gnb_html_source import GnbHtmlSource

            return GnbHtmlSource(http_client=http_client)

    raise ValueError(f"No existe un source configurado para el banco: {bank_slug}")


def get_parser(bank_slug: str) -> BaseParser:
    match bank_slug:
        case "ueno":
            from app.scraping.parsers.ueno_html_parser import UenoHtmlParser

            return UenoHtmlParser()
        case "ueno-pdf":
            settings = _experimental_settings()
            from app.scraping.clients.gemini_client import GeminiClient
            from app.scraping.clients.pdf_reader import PdfReader
            from app.scraping.experimental.ueno_parser import UenoParser

            pdf_reader = PdfReader()
            gemini_client = GeminiClient(api_key=settings.GEMINI_API_KEY, model=settings.AI_MODEL)
            return UenoParser(pdf_reader=pdf_reader, gemini_client=gemini_client)
        case "sudameris":
            from app.scraping.parsers.sudameris_html_parser import SudamerisHtmlParser

            return SudamerisHtmlParser()
        case "itau":
            from app.scraping.parsers.itau_html_parser import ItauHtmlParser

            return ItauHtmlParser()
        case "atlas":
            from app.scraping.parsers.atlas_html_parser import AtlasHtmlParser

            return AtlasHtmlParser()
        case "gnb":
            from app.scraping.parsers.gnb_html_parser import GnbHtmlParser

            return GnbHtmlParser()

    raise ValueError(f"No existe un parser configurado para el banco: {bank_slug}")
