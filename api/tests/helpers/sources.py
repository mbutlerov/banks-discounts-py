"""Offline source fixtures shared by bank adapter regressions."""
import json
from pathlib import Path

from app.scraping.parsers.pdf_offers import PdfPage

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def parser_fixture(name: str) -> str:
    return (FIXTURES / "parsers" / name).read_text(encoding="utf-8")


def catalog_fixture(name: str) -> str:
    return (FIXTURES / "catalog_routing" / name).read_text(encoding="utf-8")


def alliance_fixture(name: str) -> dict:
    return json.loads((FIXTURES / "ueno_alliances" / f"{name}.json").read_text(encoding="utf-8"))


def pdf_fixture(name: str) -> tuple[list[PdfPage], str]:
    value = json.loads(parser_fixture(name + ".json"))
    return [PdfPage(**page) for page in value["pages"]], value["source_url"]


class FakeHttp:
    def __init__(self, responses: dict[str, str | bytes | Exception]):
        self.responses = responses
        self.urls = []

    def get_bytes(self, url):
        self.urls.append(url)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value.encode("utf-8") if isinstance(value, str) else value
