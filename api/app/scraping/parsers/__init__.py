"""Import bank parser modules explicitly; package import has no adapter side effects."""

__all__: list[str] = []


def __getattr__(name: str):
    if name == "UenoParser":
        from app.scraping.experimental.ueno_parser import UenoParser

        return UenoParser
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
