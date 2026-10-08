"""Acquire official terms and adherent lists before pure/offline parsing."""
from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.scraping.clients.http_client import HttpClient, HttpClientError
from app.scraping.schemas import ScrapedSource
from app.scraping.utils.schedule import normalize

logger = logging.getLogger(__name__)
_EXTENSIONS = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def official_document_url(value: str, base: str, bank_domain: str) -> str | None:
    try:
        parsed = urlparse(urljoin(base, value.strip()))
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (host == bank_domain or host.endswith("." + bank_domain)):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.username or parsed.password or port not in {None, 80, 443}:
        return None
    return urlunparse(parsed._replace(fragment=""))


def attachment_mime(url: str) -> str | None:
    path = urlparse(url).path.lower()
    return next((mime for extension, mime in _EXTENSIONS.items() if path.endswith(extension)), None)


def image_mime(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def attach_bank_documents(source: ScrapedSource, client: HttpClient, bank_domain: str, *, include_images: bool = False) -> None:
    """Read links including Atlas modal data attributes; do not follow adverts.

    Every download uses the shared client, so snapshots and replay also include
    PDF/image annexes. Query strings and relative document links are preserved.
    """
    soup = BeautifulSoup(source.text or "", "html.parser")
    links = [(str(a["href"]), a.get_text(" ", strip=True)) for a in soup.find_all("a", href=True)]
    links.extend((str(card.get("data-boton-url", "")), str(card.get("data-boton-texto", "")))
                 for card in soup.select("[data-boton-url]") if card.get("data-boton-url"))
    seen = {document.source_url for document in source.documents}
    for value, label in links:
        url = official_document_url(value, source.source_url, bank_domain)
        mime = attachment_mime(url) if url else None
        if not url or not mime or (mime != "application/pdf" and not include_images) or url in seen:
            continue
        seen.add(url)
        role = "adherents" if "adherid" in normalize(label) else "terms"
        if mime == "application/pdf" and "pdf_url" not in source.metadata:
            source.metadata["pdf_url"] = url
        if role == "adherents":
            source.metadata["adherents_url"] = url
        try:
            content = client.get_bytes(url)
            if mime == "application/pdf" and not content.lstrip().startswith(b"%PDF"):
                source.metadata["document_error"] = "invalid_pdf_response"
                logger.warning("El anexo de %s no contiene un PDF", source.source_url)
                continue
            if mime.startswith("image/"):
                detected_mime = image_mime(content)
                if not detected_mime:
                    source.metadata["document_error"] = "invalid_image_response"
                    logger.warning("El anexo de %s no contiene una imagen reconocida", source.source_url)
                    continue
                mime = detected_mime
            source.documents.append(ScrapedSource(
                source_type="pdf" if mime == "application/pdf" else "image",
                source_url=url, content=content, mime_type=mime,
                metadata={"role": role, "label": label},
            ))
        except HttpClientError:
            source.metadata["document_error"] = "download_failed"
            logger.warning("No se pudo adquirir un anexo de %s", source.source_url)
