"""Compatibility import; production execution uses app.scraping.runner."""
from app.scraping.experimental.service import ScrapingService

__all__ = ["ScrapingService"]
