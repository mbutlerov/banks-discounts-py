"""Compatibility namespace for explicitly imported experimental prompts."""

__all__: list[str] = []


def __getattr__(name: str):
    if name == "build_ueno_page_structuring_prompt":
        from app.scraping.experimental.prompts.ueno_page_structuring_prompt import build_ueno_page_structuring_prompt

        return build_ueno_page_structuring_prompt
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
