"""Compatibility import for the explicitly selected historical PDF source."""
from app.scraping.experimental.ueno_source import UenoSource, UenoSourceError

__all__ = ["UenoSource", "UenoSourceError"]
