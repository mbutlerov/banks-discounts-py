"""Shared acquisition rejects unsafe links and invalid document responses."""
from app.scraping.clients.http_client import HttpClientError
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.bank_documents import attach_bank_documents, official_document_url
from tests.helpers.sources import FakeHttp

ATLAS = "https://www.bancoatlas.com.py/web/beneficios"
SUDAMERIS = "https://www.sudameris.com.py/beneficios/destacado/875/detalle"

def test_document_links_are_scoped_to_the_official_bank():
    assert official_document_url("https://sudameris.com.py/terms.pdf", SUDAMERIS, "sudameris.com.py")
    for url in ["https://sudameris.com.py.evil.test/terms.pdf", "https://evil.test/sudameris.com.py/terms.pdf",
                "https://user:password@sudameris.com.py/terms.pdf", "javascript:alert(1)",
                "https://www.sudameris.com.py:invalid/terms.pdf", "https://[invalid/terms.pdf"]:
        assert official_document_url(url, SUDAMERIS, "sudameris.com.py") is None


def test_invalid_pdf_or_failed_download_is_not_silently_complete():
    pdf = "https://www.sudameris.com.py/storage/terms.pdf"
    for value, expected in [(b"<html>Access denied</html>", "invalid_pdf_response"), (HttpClientError("HTTP 403"), "download_failed")]:
        source = ScrapedSource("html", SUDAMERIS, text=f'<a href="{pdf}">Bases y condiciones</a>')
        attach_bank_documents(source, FakeHttp({pdf: value}), "sudameris.com.py")
        assert source.metadata["document_error"] == expected
        assert source.documents == []


def test_image_link_returning_html_is_not_an_image_annex():
    image = "https://www.bancoatlas.com.py/web/shops.png"
    source = ScrapedSource("html", ATLAS, text=f'<div data-boton-url="{image}" data-boton-texto="Locales adheridos"></div>')
    attach_bank_documents(source, FakeHttp({image: b"<html>Temporary maintenance</html>"}), "bancoatlas.com.py", include_images=True)
    assert source.metadata["document_error"] == "invalid_image_response"
    assert source.documents == []
