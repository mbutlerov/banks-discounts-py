"""Acquire the official GNB portal, its public Angular API and legal PDFs."""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.scraping.base import BaseSource
from app.scraping.clients.http_client import HttpClient, HttpClientError
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.bank_documents import official_document_url
from app.scraping.sources.helpers import SourceDiscoveryError, attach_pdfs

logger = logging.getLogger(__name__)
OFFICIAL_CARDS_URL = "https://www.bancognb.com.py/public/tarjetas-credito.jsp"
PORTAL_URL = "https://www.beneficiosbancognb.com.py/v2/beneficios"
PUBLIC_API_URL = "https://www.beneficiosbancognb.com.py/v2/apis/rewards/rewards/v1/benefits"
API_PAGE_SIZE = 999
DETAIL_RE = re.compile(r"/(?:v2/)?beneficios/(?:categorias/)?(\d+)/?$")


class GnbHtmlSource(BaseSource):
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._owns_client = http_client is None
        self._http_client = http_client or HttpClient(
            httpx_fallback_hosts=("www.beneficiosbancognb.com.py",),
            httpx_default_user_agent_on_fallback=True,
        )
        from app.core.config import settings
        self._max_details = settings.SCRAPING_MAX_DETAILS_PER_BANK

    def fetch(self) -> list[ScrapedSource]:
        try:
            return self._fetch()
        finally:
            if self._owns_client:
                self._http_client.close()

    def _fetch(self) -> list[ScrapedSource]:
        # Resolve the catalogue using the bank's own public link. Do not use
        # QA mirrors, alternate hostnames, or cached third-party offers.
        # The bank's card page may link to the old catalogue root. The current
        # production entry is /v2/beneficios, as linked by the user's browser.
        # A missing auxiliary card page must not conceal the real portal result.
        try:
            self._http_client.get_bytes(OFFICIAL_CARDS_URL)
        except HttpClientError:
            logger.warning("No se pudo consultar la página auxiliar de tarjetas GNB")
        portal = PORTAL_URL
        queue, visited, details = [portal], set(), {}
        while queue:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            raw = self._http_client.get_bytes(url)
            soup = BeautifulSoup(raw.decode("utf-8", "replace"), "html.parser")
            if url == portal and soup.find("app-root") is not None:
                return self._fetch_api()
            for a in soup.find_all("a", href=True):
                candidate = urljoin(portal, str(a["href"]))
                parsed = urlparse(candidate)
                if parsed.hostname != "www.beneficiosbancognb.com.py":
                    continue
                match = DETAIL_RE.search(parsed.path)
                if match:
                    details[candidate] = (match[1], a.get_text(" ", strip=True))
                elif "/beneficios/categorias" in parsed.path or re.search(r"(?:^|[?&])page=\d+", candidate):
                    if candidate not in visited and candidate not in queue:
                        queue.append(candidate)
            if len(visited) > 100:
                raise SourceDiscoveryError("GNB superó el límite de páginas de descubrimiento")
        if not details:
            raise SourceDiscoveryError("El catálogo GNB no expuso detalles de promoción reconocidos; requiere revisión del HTML")
        sources = []
        for url, (promo_id, title) in details.items():
            try:
                source = ScrapedSource(source_type="html", source_url=url,
                                        text=self._http_client.get_bytes(url).decode("utf-8", "replace"),
                                        metadata={"bank_slug": "gnb", "promo_id": promo_id, "link_text": title})
                attach_pdfs(source, self._http_client)
                sources.append(source)
            except HttpClientError:
                logger.warning("No se pudo obtener un detalle GNB")
        return sources

    def _fetch_api(self) -> list[ScrapedSource]:
        """The verified public list contains full descriptions and legal links.

        Preserve the original list as snapshot evidence instead of requesting
        the same record again through every detail endpoint.
        """
        records: dict[int, tuple[dict, str]] = {}
        expected_total: int | None = None
        expected_pages: int | None = None
        limited = False
        for page in range(100):
            url = f"{PUBLIC_API_URL}/benefits?pageSize={API_PAGE_SIZE}&paginationKey={page}"
            try:
                payload = json.loads(self._http_client.get_bytes(url))
            except (ValueError, UnicodeError) as exc:
                raise SourceDiscoveryError("La API pública GNB no devolvió JSON válido") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise SourceDiscoveryError("Cambió la estructura del catálogo público GNB")
            pagination = payload.get("pagination")
            if not isinstance(pagination, dict) or any(type(pagination.get(key)) is not int for key in ("page", "totalPages", "totalElements")):
                raise SourceDiscoveryError("GNB no expuso una paginación verificable")
            total, pages = pagination["totalElements"], pagination["totalPages"]
            if pagination["page"] != page or total < 0 or not 1 <= pages <= 100 or page >= pages:
                raise SourceDiscoveryError("La paginación GNB es inconsistente o excede el límite")
            if expected_total is None:
                expected_total = total
                expected_pages = pages
            elif expected_total != total or expected_pages != pages:
                raise SourceDiscoveryError("El catálogo GNB cambió durante la paginación; reintentar")
            for row in payload["data"]:
                if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 or not isinstance(row.get("title"), str) or not row["title"].strip():
                    raise SourceDiscoveryError("Registro GNB sin identidad comercial verificable")
                if type(row.get("status")) is not bool:
                    raise SourceDiscoveryError("Registro GNB sin estado de publicación verificable")
                if row["id"] in records:
                    if records[row["id"]][0] != row:
                        raise SourceDiscoveryError("GNB devolvió registros duplicados incompatibles")
                    continue
                records[row["id"]] = (row, url)
            if len(records) >= self._max_details and page + 1 < pages:
                limited = True
                break
            if page + 1 >= pages:
                break
        else:
            raise SourceDiscoveryError("GNB superó el límite de páginas de descubrimiento")
        if not records:
            raise SourceDiscoveryError("El catálogo público GNB está vacío; se conservan los datos anteriores")
        if not limited and len(records) != expected_total:
            raise SourceDiscoveryError("La API GNB no devolvió todos los registros anunciados")
        limited = limited or len(records) > self._max_details
        sources = []
        for row, api_url in list(records.values())[:self._max_details]:
            source = ScrapedSource(
                source_type="json", source_url=f"{PORTAL_URL}/categorias/{row['id']}",
                text=json.dumps({"data": row}, ensure_ascii=False), mime_type="application/json",
                metadata={"bank_slug": "gnb", "promo_id": str(row["id"]), "link_text": row["title"],
                          "source_type": "gnb_api", "api_url": api_url, "catalogue_url": PORTAL_URL},
            )
            if limited:
                source.metadata["discovery_warning"] = "gnb_detail_limit_reached"
            self._attach_api_pdf(source, row.get("termsAndConditionsImage"), role="terms")
            # Follow only public, same-bank adherent PDF links. External merchant
            # websites remain referenced in the original record, not crawled.
            if "adherid" in str(row.get("linkDescription", "")).lower():
                self._attach_pdf_url(source, str(row.get("link", "")), role="adherents")
            sources.append(source)
        return sources

    def _attach_api_pdf(self, source: ScrapedSource, value, *, role: str) -> None:
        if value is None:
            return
        # This is a PHP-serialized file descriptor, not executable PHP. Read
        # only the documented path key; never deserialize objects or trust
        # byte lengths in the bank's occasionally misencoded file names.
        descriptor = value.get("imageLink") if isinstance(value, dict) else None
        match = re.search(r's:4:"path";s:\d+:"([^"\r\n]+)";', descriptor) if isinstance(descriptor, str) else None
        if not match or not match[1].startswith("imagenes/"):
            source.metadata["document_error"] = "gnb_unrecognized_legal_file"
            return
        self._attach_pdf_url(source, match[1], role=role)

    def _attach_pdf_url(self, source: ScrapedSource, value: str, *, role: str) -> None:
        url = official_document_url(value, "https://www.beneficiosbancognb.com.py/", "beneficiosbancognb.com.py")
        if not url or not urlparse(url).path.lower().endswith(".pdf"):
            source.metadata["document_error"] = "gnb_unrecognized_legal_file"
            return
        if role == "terms":
            source.metadata["pdf_url"] = url
        else:
            source.metadata["adherents_url"] = url
        if any(document.source_url == url for document in source.documents):
            return
        try:
            content = self._http_client.get_bytes(url)
            if not content.lstrip().startswith(b"%PDF"):
                source.metadata["document_error"] = "invalid_pdf_response"
                return
            source.documents.append(ScrapedSource(source_type="pdf", source_url=url, content=content,
                                                  mime_type="application/pdf", metadata={"role": role}))
        except HttpClientError:
            source.metadata["document_error"] = "download_failed"
            logger.warning("No se pudo adquirir un documento legal GNB de %s", source.source_url)
