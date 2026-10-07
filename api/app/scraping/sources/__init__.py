"""Import bank source modules explicitly; package import has no adapter side effects."""

__all__: list[str] = []


def __getattr__(name: str):
    if name == "UenoSource":
        from app.scraping.experimental.ueno_source import UenoSource

        return UenoSource
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
