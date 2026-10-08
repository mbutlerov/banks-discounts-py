"""Compatibility import; canonical Ueno ingestion uses ueno_html_parser."""
from app.scraping.experimental.ueno_parser import UenoParser

__all__ = ["UenoParser"]
