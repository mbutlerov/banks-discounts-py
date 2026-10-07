"""Historical non-persistent service; production entry points use runner.py."""
from __future__ import annotations

from app.scraping.factories import get_parser, get_source
from app.scraping.schemas import ScrapedPromotion


class ScrapingService:
    def run(self, bank_slug: str) -> list[ScrapedPromotion]:
        source = get_source(bank_slug)
        parser = get_parser(bank_slug)

        promotions: list[ScrapedPromotion] = []
        for scraped_source in source.fetch():
            promotions.extend(parser.parse(scraped_source))

        return promotions
