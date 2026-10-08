from __future__ import annotations

import logging
from datetime import date

from app.scraping.base import BaseParser
from app.scraping.clients.gemini_client import GeminiClient, GeminiClientRequestError
from app.scraping.clients.pdf_reader import PdfReader
from app.scraping.experimental.prompts.ueno_page_structuring_prompt import (
    build_ueno_page_structuring_prompt,
)
from app.scraping.schemas import ScrapedPromotion, ScrapedSource

logger = logging.getLogger(__name__)


class UenoParser(BaseParser):
    MAX_PAGES_TO_PROCESS = 30
    MAX_CHARS_PER_PAGE = 1500

    def __init__(
        self,
        pdf_reader: PdfReader,
        gemini_client: GeminiClient,
    ) -> None:
        self._pdf_reader = pdf_reader
        self._gemini_client = gemini_client

    def parse(self, source: ScrapedSource) -> list[ScrapedPromotion]:
        if source.content is None:
            raise ValueError("La fuente de ueno no contiene contenido en bytes.")

        pages = self._pdf_reader.extract_pages_text(source.content)
        pages = self._filter_pages(pages)
        pages = pages[:self.MAX_PAGES_TO_PROCESS]

        promotions: list[ScrapedPromotion] = []

        for page in pages:
            page_text = self._truncate_page_text(page.text)
            logger.debug("Procesando página %d", page.page_number)

            if not page_text.strip():
                continue

            prompt = build_ueno_page_structuring_prompt(
                page_number=page.page_number,
                page_text=page_text,
            )

            try:
                result = self._gemini_client.generate_json(
                    prompt=prompt,
                    max_output_tokens=512,
                )
                logger.debug("Gemini página %d: %s", page.page_number, result.parsed_json)
            except GeminiClientRequestError as exc:
                logger.warning("GeminiClientRequestError en página %d: %s", page.page_number, exc)
                continue
            except Exception as exc:
                logger.error("Error inesperado en página %d: %s: %s", page.page_number, type(exc).__name__, exc)
                continue

            if not isinstance(result.parsed_json, dict):
                logger.warning("parsed_json no es dict en página %d: %r", page.page_number, result.parsed_json)
                continue

            has_promotions = bool(result.parsed_json.get("has_promotions"))
            items = result.parsed_json.get("items", [])

            logger.debug("Página %d -> has_promotions=%s, items=%d", page.page_number, has_promotions, len(items) if isinstance(items, list) else 0)

            if not has_promotions or not isinstance(items, list):
                continue

            for item in items:
                if not isinstance(item, dict):
                    continue

                title = self._clean_string(item.get("title"))
                if not title:
                    continue

                promotions.append(
                    ScrapedPromotion(
                        bank_slug="ueno",
                        title=title,
                        description=self._clean_string(item.get("description")),
                        category_name=None,
                        merchant_name=None,
                        campaign_name=None,
                        benefit_type=self._clean_string(item.get("benefit_type")),
                        mechanic_type=None,
                        start_date=self._parse_date(item.get("start_date")),
                        end_date=self._parse_date(item.get("end_date")),
                        terms_summary=None,
                        raw_text=page_text,
                        metadata={
                            "page_number": str(page.page_number),
                            "source_type": source.source_type,
                            "source_url": source.source_url,
                        },
                    )
                )

        return promotions

    def _filter_pages(self, pages: list) -> list:
        filtered_pages: list = []

        for page in pages:
            text = page.text.strip()
            if not text:
                continue

            lowered_text = text.lower()

            if len(text) < 80:
                continue

            if "índice" in lowered_text or "indice" in lowered_text:
                continue

            filtered_pages.append(page)

        return filtered_pages

    def _truncate_page_text(self, text: str) -> str:
        text = text.strip()

        if len(text) <= self.MAX_CHARS_PER_PAGE:
            return text

        return text[: self.MAX_CHARS_PER_PAGE]

    @staticmethod
    def _clean_string(value: object) -> str | None:
        if not isinstance(value, str):
            return None

        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _parse_date(value: object) -> date | None:
        if not isinstance(value, str):
            return None

        value = value.strip()
        if not value:
            return None

        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
