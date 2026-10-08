"""Bank-specific ingestion. Import services explicitly to keep pure parsers isolated."""

__all__: list[str] = []


def __getattr__(name: str):
    if name == "ScrapingService":
        from app.scraping.experimental.service import ScrapingService

        return ScrapingService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
