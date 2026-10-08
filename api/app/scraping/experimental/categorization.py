from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.database.models.category import Category
from app.database.models.promotion import Promotion
from app.scraping.clients.gemini_client import GeminiClient, GeminiClientError
from app.scraping.experimental.prompts.categorization_prompt import (
    CATEGORIZATION_SCHEMA,
    CATEGORY_SLUGS,
    build_categorization_prompt,
)

logger = logging.getLogger(__name__)

_GEMINI_DELAY_SECONDS = 4


def run_categorization(db: Session, *, bank_slug: str | None = None) -> dict:
    """Clasifica con Gemini las promos que quedaron en categoría 'other'.

    Retorna un resumen: found, updated, unchanged, errors.
    """
    gemini_client = _make_gemini_client()
    if not gemini_client:
        return {"error": "GEMINI_API_KEY no configurada."}

    # Cargar IDs de categorías en memoria para no hacer query por cada promo
    category_map: dict[str, int] = {
        c.slug: c.id
        for c in db.query(Category).all()
    }
    other_id = category_map.get("other")
    if other_id is None:
        return {"error": "Categoría 'other' no encontrada. Ejecutá el seed primero."}

    promotions = _find_uncategorized(db, other_id, bank_slug=bank_slug)
    logger.info("Categorización: %d promos en 'other'.", len(promotions))

    results = {"found": len(promotions), "updated": 0, "unchanged": 0, "errors": 0}

    for i, promo in enumerate(promotions):
        if i > 0:
            time.sleep(_GEMINI_DELAY_SECONDS)

        try:
            slug = _classify_with_gemini(promo, gemini_client)

            if slug and slug != "other" and slug in category_map:
                promo.category_id = category_map[slug]
                logger.info("'%s' → %s", promo.title[:50], slug)
                results["updated"] += 1
            else:
                results["unchanged"] += 1

        except Exception as exc:
            logger.error("Error categorizando '%s': %s", promo.title[:50], exc)
            results["errors"] += 1

    db.commit()
    logger.info("Categorización finalizada: %s", results)
    return results


def _find_uncategorized(
    db: Session,
    other_id: int,
    *,
    bank_slug: str | None,
) -> list[Promotion]:
    from app.database.models.bank import Bank

    q = (
        db.query(Promotion)
        .join(Promotion.bank)
        .filter(Promotion.category_id == other_id)
    )
    if bank_slug:
        q = q.filter(Bank.slug == bank_slug)
    return q.all()


def _classify_with_gemini(
    promo: Promotion,
    gemini_client: GeminiClient,
) -> str | None:
    prompt = build_categorization_prompt(promo.title, promo.description)
    try:
        result = gemini_client.generate_json(
            prompt=prompt,
            response_json_schema=CATEGORIZATION_SCHEMA,
            max_output_tokens=64,
        )
        if result.parsed_json and isinstance(result.parsed_json, dict):
            slug = result.parsed_json.get("category_slug")
            if slug in CATEGORY_SLUGS:
                return slug
    except GeminiClientError as exc:
        logger.warning("Gemini falló para '%s': %s", promo.title[:50], exc)
    return None


def _make_gemini_client() -> GeminiClient | None:
    from app.core.config import settings
    from app.scraping.clients.gemini_client import GeminiClient, GeminiClientConfigError
    if not settings.ENABLE_AI_ENRICHMENT:
        raise ValueError("La categorización experimental requiere ENABLE_AI_ENRICHMENT=true")
    try:
        return GeminiClient(api_key=settings.GEMINI_API_KEY)
    except GeminiClientConfigError:
        return None
