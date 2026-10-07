from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader as PyPdfReader

from app.scraping.schemas import ScrapedPage
from app.scraping.utils.encoding import fix_mojibake


class PdfReaderError(Exception):
    """Error base para la lectura de PDFs."""


class PdfReader:
    def extract_text(self, pdf_content: bytes) -> str:
        pages = self.extract_pages_text(pdf_content)
        raw = "\n\n".join(page.text for page in pages if page.text.strip())
        return fix_mojibake(raw)

    def extract_pages_text(self, pdf_content: bytes) -> list[ScrapedPage]:
        try:
            reader = PyPdfReader(BytesIO(pdf_content))
        except Exception as exc:
            raise PdfReaderError("No se pudo abrir el PDF.") from exc

        pages: list[ScrapedPage] = []

        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            cleaned_text = text.strip()

            pages.append(
                ScrapedPage(
                    page_number=index,
                    text=cleaned_text,
                    metadata={},
                )
            )

        return pages