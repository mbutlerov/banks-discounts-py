"""Discover the official category catalogue and its server-rendered detail calls."""
from __future__ import annotations

import html
import logging
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from app.scraping.base import BaseSource
from app.scraping.clients.http_client import HttpClient, HttpClientError
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.helpers import SourceDiscoveryError
from app.scraping.utils.encoding import fix_mojibake

logger = logging.getLogger(__name__)

MAIN_URL = "https://www.itau.com.py/beneficios"
LEGACY_URL = "https://www.itau.com.py/beneficios2/"
BASE_URL = "https://www.itau.com.py"
DETAIL_URL = BASE_URL + "/beneficios2/Detalle?b={b}&c={c}"
CATEGORY_RE = re.compile(r"^/beneficios2/categoria/\d+/?$", re.I)
# The modal's buscar(b,c,name) call is present on every card, including cards
# inside hidden pagination groups. Read the DOM, without running its scripts.
BUSCAR_RE = re.compile(
    r"\bbuscar\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]\s*,\s*"
    r"(?P<quote>['\"])(?P<name>(?:\\.|(?!(?P=quote)).)*)(?P=quote)\s*\)", re.S
)


def _official_url(value: str, base: str) -> str | None:
    candidate = urljoin(base, value)
    parsed = urlparse(candidate)
    if parsed.scheme not in ("https", "http") or parsed.hostname not in ("itau.com.py", "www.itau.com.py") or parsed.username or parsed.password:
        return None
    return candidate.split("#", 1)[0]


def extract_detail_calls(soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    """Return stable IDs from real card handlers or direct official detail links."""
    result: dict[tuple[str, str], str] = {}
    for element in soup.find_all(True):
        handler = html.unescape(str(element.get("onclick", "")))
        for match in BUSCAR_RE.finditer(handler):
            name = re.sub(r"\\(['\"\\])", r"\1", match["name"])
            result.setdefault((match[1], match[2]), fix_mojibake(html.unescape(name)))
        href = element.get("href")
        if not href:
            continue
        candidate = _official_url(str(href), BASE_URL)
        if not candidate or urlparse(candidate).path.lower().rstrip("/") != "/beneficios2/detalle":
            continue
        query = parse_qs(urlparse(candidate).query)
        b, c = query.get("b", [""])[0], query.get("c", [""])[0]
        if b.isdigit() and c.isdigit():
            result.setdefault((b, c), fix_mojibake(element.get_text(" ", strip=True)))
    return [(b, c, name) for (b, c), name in result.items()]


class ItauHtmlSource(BaseSource):
    def __init__(self, http_client: HttpClient | None = None, max_details: int = 500, max_category_pages: int = 80) -> None:
        self._http_client = http_client or HttpClient()
        self._max_details = max_details
        self._max_category_pages = max_category_pages

    def fetch(self) -> list[ScrapedSource]:
        warnings: list[str] = []
        try:
            landing = self._http_client.get_bytes(MAIN_URL)
            catalogue_url = MAIN_URL
        except HttpClientError:
            landing = self._http_client.get_bytes(LEGACY_URL)
            catalogue_url = LEGACY_URL
            warnings.append("La entrada /beneficios no respondió; se usó la entrada oficial /beneficios2/")

        category_urls: dict[str, str] = {}
        queue: list[str] = []
        visited: set[str] = set()
        params: dict[tuple[str, str], str] = {}
        categories_by_key: dict[tuple[str, str], list[str]] = {}

        def discover(soup: BeautifulSoup, page_url: str, category_name: str = "") -> None:
            unrecognized = 0
            for card in soup.select(".item-oferta"):
                handler = html.unescape(str(card.get("onclick", "")))
                direct = _official_url(str(card.get("href", "")), page_url)
                direct_query = parse_qs(urlparse(direct).query) if direct else {}
                if not BUSCAR_RE.search(handler) and not (
                    direct and urlparse(direct).path.lower().rstrip("/") == "/beneficios2/detalle"
                    and direct_query.get("b", [""])[0].isdigit() and direct_query.get("c", [""])[0].isdigit()
                ):
                    unrecognized += 1
            if unrecognized:
                warnings.append(f"{unrecognized} tarjetas con rutas no reconocidas en {page_url}")
            for a in soup.find_all("a", href=True):
                candidate = _official_url(str(a["href"]), page_url)
                if not candidate or not CATEGORY_RE.fullmatch(urlparse(candidate).path):
                    continue
                # Categories have no query parameters. Avoid repeated requests
                # for tracking variants and anchor links to the same category.
                candidate = candidate.split("?", 1)[0].rstrip("/")
                if candidate not in category_urls:
                    category_urls[candidate] = fix_mojibake(a.get_text(" ", strip=True))
                    queue.append(candidate)
            for b, c, name in extract_detail_calls(soup):
                key = (b, c)
                params.setdefault(key, name)
                categories = categories_by_key.setdefault(key, [])
                if category_name and category_name not in categories:
                    categories.append(category_name)

        discover(BeautifulSoup(landing.decode("utf-8", "replace"), "html.parser"), catalogue_url)
        if not category_urls and not params:
            raise SourceDiscoveryError("La entrada Itaú no contiene categorías ni detalles reconocidos")

        while queue and len(visited) < self._max_category_pages:
            category_url = queue.pop(0)
            if category_url in visited:
                continue
            visited.add(category_url)
            try:
                raw = self._http_client.get_bytes(category_url)
                discover(BeautifulSoup(raw.decode("utf-8", "replace"), "html.parser"), category_url, category_urls[category_url])
            except HttpClientError:
                warnings.append(f"No se pudo adquirir la categoría {category_url}")
                logger.warning("No se pudo obtener categoría Itaú %s", category_url)

        if queue:
            warnings.append(f"Catálogo limitado a {self._max_category_pages} páginas de categorías")
        if not params:
            raise SourceDiscoveryError("El catálogo Itaú no contiene detalles buscar(b,c) reconocidos")
        if len(params) > self._max_details:
            warnings.append(f"Catálogo limitado a {self._max_details} de {len(params)} detalles; recorrido parcial")

        sources: list[ScrapedSource] = []
        for (b, c), name in list(params.items())[:self._max_details]:
            detail_url = DETAIL_URL.format(b=b, c=c)
            try:
                detail = self._http_client.get_bytes(detail_url)
                categories = categories_by_key[(b, c)]
                sources.append(ScrapedSource(
                    source_type="html", source_url=detail_url,
                    text=detail.decode("utf-8", "replace"),
                    metadata={"partner_name": name, "category_name": categories[0] if categories else "",
                              "source_categories": " | ".join(categories), "catalogue_url": catalogue_url,
                              "b": b, "c": c},
                ))
            except HttpClientError:
                warnings.append(f"No se pudo adquirir el detalle {detail_url}")
                logger.warning("No se pudo obtener detalle Itaú %s", detail_url)
        if not sources:
            raise SourceDiscoveryError("No se pudo adquirir ningún detalle Itaú descubierto")
        if warnings:
            sources[0].metadata["discovery_warning"] = " | ".join(warnings)
        logger.info("Itaú: %d categorías, %d detalles descubiertos, %d adquiridos", len(visited), len(params), len(sources))
        return sources
