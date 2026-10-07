"""Compatibility import for the opt-in historical enrichment job."""
from app.scraping.experimental.enrichment import run_enrichment

__all__ = ["run_enrichment"]
