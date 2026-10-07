from __future__ import annotations

import logging
import time
from datetime import date

from bs4 import BeautifulSoup
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database.models.bank import Bank
from app.database.models.promotion import Promotion
from app.scraping.clients.gemini_client import GeminiClient, GeminiClientError
from app.scraping.clients.http_client import HttpClient, HttpClientError
from app.scraping.clients.pdf_reader import PdfReader, PdfReaderError
from app.scraping.experimental.prompts.enrichment_prompt import ENRICHMENT_SCHEMA, build_enrichment_prompt

logger = logging.getLogger(__name__)

# Campos críticos que queremos completar
_CRITICAL_FIELDS = ["discount_percentage", "start_date", "end_date"]

# Pausa entre llamadas a Gemini para respetar el rate limit del tier gratuito
_GEMINI_DELAY_SECONDS = 4


def run_enrichment(
    db: Session,
    *,
    bank_slug: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Busca promos con datos incompletos y las enriquece con Gemini.

    Retorna un resumen: total encontradas, enriquecidas, sin cambios, errores.
    """
    http_client = HttpClient()
    pdf_reader = PdfReader()
    gemini_client = _make_gemini_client()
    if not gemini_client:
        return {"error": "GEMINI_API_KEY no configurada."}

    promotions = _find_incomplete(db, bank_slug=bank_slug)
    logger.info("Enrichment: %d promos incompletas encontradas.", len(promotions))

    results = {"found": len(promotions), "enriched": 0, "unchanged": 0, "errors": 0}

    for i, promo in enumerate(promotions):
        if i > 0:
            time.sleep(_GEMINI_DELAY_SECONDS)

        try:
            enriched = _enrich_one(promo, http_client, pdf_reader, gemini_client)
            if enriched and not dry_run:
                changed = _apply(db, promo, enriched)
                if changed:
                    results["enriched"] += 1
                else:
                    results["unchanged"] += 1
            elif not enriched:
                results["unchanged"] += 1
        except Exception as exc:
            logger.error("Error enriqueciendo promo %s: %s", promo.slug, exc)
            results["errors"] += 1

    if not dry_run:
        db.commit()

    logger.info("Enrichment finalizado: %s", results)
    return results


def _find_incomplete(db: Session, *, bank_slug: str | None) -> list[Promotion]:
    q = (
        db.query(Promotion)
        .join(Promotion.bank)
        .filter(
            Promotion.status == "active",
            Promotion.metadata_jsonb["source_url"].astext.isnot(None),
            or_(
                Promotion.discount_percentage.is_(None),
                Promotion.start_date.is_(None),
                Promotion.end_date.is_(None),
            ),
        )
    )
    if bank_slug:
        q = q.filter(Bank.slug == bank_slug)
    return q.all()


def _enrich_one(
    promo: Promotion,
    http_client: HttpClient,
    pdf_reader: PdfReader,
    gemini_client: GeminiClient,
) -> dict | None:
    meta = promo.metadata_jsonb or {}
    source_url = meta.get("source_url")
    pdf_url = meta.get("pdf_url")

    content_parts: list[str] = []

    # Incluir descripción ya guardada si tiene contenido
    if promo.description:
        content_parts.append(promo.description)

    # Re-fetchar la página fuente si la descripción es escasa
    if source_url and len(promo.description or "") < 100:
        html_text = _fetch_html_text(http_client, source_url)
        if html_text:
            content_parts.append(html_text)

    # Incluir texto del PDF si hay
    if pdf_url:
        pdf_text = _fetch_pdf_text(http_client, pdf_reader, pdf_url)
        if pdf_text:
            content_parts.append(pdf_text)

    content = "\n\n".join(content_parts).strip()
    if not content:
        logger.debug("Sin contenido para enriquecer: %s", promo.slug)
        return None

    missing = _missing_fields(promo)
    prompt = build_enrichment_prompt(
        title=promo.title,
        benefit_type=promo.benefit_type,
        missing_fields=missing,
        content=content,
    )

    try:
        result = gemini_client.generate_json(
            prompt=prompt,
            response_json_schema=ENRICHMENT_SCHEMA,
            max_output_tokens=256,
        )
        if result.parsed_json and isinstance(result.parsed_json, dict):
            logger.debug("Gemini enriqueció %s: %s", promo.slug, result.parsed_json)
            return result.parsed_json
    except GeminiClientError as exc:
        logger.warning("Gemini falló para %s: %s", promo.slug, exc)

    return None


def _apply(db: Session, promo: Promotion, data: dict) -> bool:
    changed = False
    meta = dict(promo.metadata_jsonb or {})

    if promo.discount_percentage is None and data.get("discount_percentage"):
        promo.discount_percentage = int(data["discount_percentage"])
        changed = True

    new_start: date | None = None
    new_end: date | None = None

    if promo.start_date is None and data.get("start_date"):
        try:
            new_start = date.fromisoformat(data["start_date"])
        except ValueError:
            pass

    if promo.end_date is None and data.get("end_date"):
        try:
            new_end = date.fromisoformat(data["end_date"])
        except ValueError:
            pass

    # Rechazar si start > end (alucinación de Gemini)
    effective_start = new_start or promo.start_date
    effective_end = new_end or promo.end_date
    if effective_start and effective_end and effective_start > effective_end:
        logger.warning(
            "Fechas inválidas descartadas para %s: start=%s > end=%s",
            promo.slug, effective_start, effective_end,
        )
        new_start = None
        new_end = None

    if new_start:
        promo.start_date = new_start
        changed = True
    if new_end:
        promo.end_date = new_end
        changed = True

    if not meta.get("installments") and data.get("installments"):
        meta["installments"] = str(int(data["installments"]))
        changed = True

    if not meta.get("applicable_cards") and data.get("applicable_cards"):
        cards = data["applicable_cards"]
        if isinstance(cards, list) and cards:
            meta["applicable_cards"] = ", ".join(str(c) for c in cards)
            changed = True

    if changed:
        promo.metadata_jsonb = meta

    return changed


def _missing_fields(promo: Promotion) -> list[str]:
    missing = []
    if promo.discount_percentage is None and promo.benefit_type in ("discount", "cashback"):
        missing.append("discount_percentage")
    if promo.start_date is None:
        missing.append("start_date")
    if promo.end_date is None:
        missing.append("end_date")
    meta = promo.metadata_jsonb or {}
    if not meta.get("applicable_cards"):
        missing.append("applicable_cards")
    if not meta.get("installments") and promo.benefit_type == "installments":
        missing.append("installments")
    return missing


def _fetch_html_text(http_client: HttpClient, url: str) -> str | None:
    try:
        raw = http_client.get_bytes(url)
        soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")

        # Intentar extraer solo el contenido relevante
        for selector in ("div.description-promo", "main", "article"):
            el = soup.select_one(selector)
            if el:
                return el.get_text(" ", strip=True)

        # Fallback: todo el body sin nav/footer
        for tag in soup.find_all(["nav", "footer", "script", "style"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)[:2000]
    except HttpClientError as exc:
        logger.warning("No se pudo obtener %s: %s", url, exc)
        return None


def _fetch_pdf_text(http_client: HttpClient, pdf_reader: PdfReader, url: str) -> str | None:
    try:
        raw = http_client.get_bytes(url)
        return pdf_reader.extract_text(raw)
    except (HttpClientError, PdfReaderError) as exc:
        logger.warning("No se pudo leer PDF %s: %s", url, exc)
        return None


def _make_gemini_client() -> GeminiClient | None:
    from app.core.config import settings
    from app.scraping.clients.gemini_client import GeminiClientConfigError
    if not settings.ENABLE_AI_ENRICHMENT:
        raise ValueError("El enriquecimiento experimental requiere ENABLE_AI_ENRICHMENT=true")
    try:
        return GeminiClient(api_key=settings.GEMINI_API_KEY)
    except GeminiClientConfigError:
        return None
