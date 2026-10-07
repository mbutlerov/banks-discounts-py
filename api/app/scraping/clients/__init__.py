"""Clients are loaded only when explicitly requested by their consumers."""
from importlib import import_module

_CLIENT_MODULES = {
    "HttpClient": "http_client",
    "PdfReader": "pdf_reader",
    "GeminiClient": "gemini_client",
}

__all__ = ["HttpClient", "PdfReader", "GeminiClient"]


def __getattr__(name: str):
    if name not in _CLIENT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{_CLIENT_MODULES[name]}"), name)
    globals()[name] = value
    return value
