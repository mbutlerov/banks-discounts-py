"""Atlas source, parser, document and catalogue regressions."""
from __future__ import annotations

import unittest
from datetime import date, timedelta
import json
from unittest.mock import patch

import pytest

from app.scraping.parsers.atlas_html_parser import AtlasHtmlParser
from app.scraping.parsers.pdf_offers import PdfPage
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.atlas_html_source import AtlasHtmlSource
from app.scraping.sources.helpers import SourceDiscoveryError
from tests.helpers.sources import FakeHttp, parser_fixture

ATLAS = "https://www.bancoatlas.com.py/web/beneficios"


class SourceContractTests(unittest.TestCase):
    def test_atlas_pagination_uses_all_cards_not_global_jsonld_rows(self):
        base = "https://www.bancoatlas.com.py/web/beneficios"
        page1 = '<div class="benefit-card" data-nombre="A" data-logo="/logo-A"></div><a href="/web/beneficios?page=2">2</a>'
        page2 = '<div class="benefit-card" data-nombre="B" data-logo="/logo-B"></div>'
        http = FakeHttp({base: page1, base + "?page=2": page2})
        self.assertEqual(len(AtlasHtmlSource(http).fetch()), 2)
        self.assertEqual(http.urls, [base, base + "?page=2"])

    def test_changed_catalogue_is_explicit_failure(self):
        with self.assertRaises(SourceDiscoveryError):
            AtlasHtmlSource(FakeHttp({"https://www.bancoatlas.com.py/web/beneficios": "<h1>Access denied</h1>"})).fetch()


class OfficialParserTests(unittest.TestCase):
    def test_atlas_conflicting_jsonld_stays_pending_and_tiers_are_bound(self):
        source = ScrapedSource("html", "https://www.bancoatlas.com.py/web/beneficios",
                               text=parser_fixture("atlas_biggie.html"), metadata={"jsonld": parser_fixture("atlas_biggie_jsonld.json")})
        promo = AtlasHtmlParser().parse(source)[0]
        self.assertTrue(all(o.validity_state == "conflict" and o.publication == "pending" for o in promo.offers))
        self.assertEqual(len(promo.offers), 2)
        self.assertEqual([o.benefits[0].percentage for o in promo.offers], [25, 20])
        self.assertEqual([o.caps[0].amount for o in promo.offers], [800000, 400000])
        self.assertEqual(promo.offers[0].caps[0].type, "purchase")
        self.assertEqual(promo.offers[0].schedule.month_days, [15])
        self.assertIsNone(promo.discount_percentage)

    def test_atlas_shared_cap_does_not_drop_special_card_variant(self):
        html = '<div class="benefit-card" data-nombre="Shopping" data-pct="20%" data-label-pct="de reintegro" data-dias="Todos los días" data-desc="con tarjetas de crédito delSol Clásica" data-extra-label="+10% de reintegro" data-extra-resto="con tarjetas Signature delSol" data-topes="Gs. 30.000.000 para tarjetas de crédito delSol" data-topes-titulo="Tope de compra mensual" data-terminos="Desde el 1 de octubre de 2026 hasta el 4 de octubre de 2026."></div>'
        offers = AtlasHtmlParser().parse(ScrapedSource("html", "https://www.bancoatlas.com.py/web/beneficios", text=html))[0].offers
        self.assertEqual(len(offers), 2)
        self.assertEqual(sorted(o.benefits[0].percentage for o in offers), [20, 30])
        self.assertTrue(all(o.caps[0].amount == 30000000 for o in offers))
        special = next(o for o in offers if o.benefits[0].percentage == 30)
        self.assertEqual(special.eligibility.cards, ["Signature Delsol"])


def stations_source(document=None):
    return ScrapedSource("html", ATLAS, text=parser_fixture("atlas_stations.html"),
                         metadata={"jsonld": parser_fixture("atlas_stations_jsonld.json")},
                         documents=[document] if document else [])


def test_atlas_modal_acquires_pdf_and_image_annexes():
    pdf, image = "https://www.bancoatlas.com.py/web/stations.pdf?download=1", "https://www.bancoatlas.com.py/web/shops.png"
    html = '<div class="benefit-card" data-nombre="Fuel" data-boton-url="/web/stations.pdf?download=1" data-boton-texto="Ver estaciones adheridas"></div><div class="benefit-card" data-nombre="Shops" data-boton-url="/web/shops.png" data-boton-texto="Ver tiendas adheridas"></div>'
    client = FakeHttp({ATLAS: html, pdf: b"%PDF-1.7\nfixture", image: b"\x89PNG\r\n\x1a\nfixture"})
    sources = AtlasHtmlSource(client).fetch()
    assert client.urls == [ATLAS, pdf, image]
    assert [s.documents[0].source_type for s in sources] == ["pdf", "image"]
    assert all(s.documents[0].metadata["role"] == "adherents" for s in sources)
    assert sources[0].metadata["adherents_url"] == pdf


def test_atlas_scanned_stations_keep_two_tiers_minimum_and_unknown_branches():
    annex = "https://www.bancoatlas.com.py/web/Estaciones%20de%20servicio%20adheridas%20sept.pdf"
    source = stations_source(ScrapedSource("pdf", annex, text=""))
    promotion = AtlasHtmlParser().parse(source)[0]
    premium, basic = promotion.offers
    assert [offer.benefits[0].percentage for offer in promotion.offers] == [25, 20]
    assert [offer.caps[0].amount for offer in promotion.offers] == [1000000, 700000]
    assert all(offer.caps[0].scope == "account" for offer in promotion.offers)
    assert all(offer.schedule.weekdays == [2] for offer in promotion.offers)
    assert "Mastercard Black" in premium.eligibility.cards and "Oro" in basic.eligibility.cards
    assert all("200.000" in "\n".join(offer.eligibility.conditions) for offer in promotion.offers)
    assert all(offer.merchant_name == "Estaciones de Servicio" for offer in promotion.offers)
    assert all(offer.eligibility.locations == [] for offer in promotion.offers)
    assert all("merchant_locations" in offer.eligibility.unknown_fields for offer in promotion.offers)
    assert all(any(evidence.source_url == annex for evidence in offer.evidence) for offer in promotion.offers)
    assert source.metadata["parse_warning"] == "annex_requires_ocr"
    assert promotion.metadata["offers_complete"] == "false"


def test_recognized_annex_table_preserves_specific_station_addresses():
    annex = "https://www.bancoatlas.com.py/web/Estaciones%20de%20servicio%20adheridas%20sept.pdf"
    source = stations_source(ScrapedSource("pdf", annex, text="fixture"))
    pages = [PdfPage(1, "Listado de estaciones adheridas", [[['Estación', 'Dirección'], ['Estación ejemplo', 'Av. Ejemplo 123']]])]
    with patch("app.scraping.parsers.atlas_html_parser.read_pdf", return_value=pages):
        offers = AtlasHtmlParser().parse(source)[0].offers
    assert all(offer.eligibility.locations == ["Estación ejemplo | Av. Ejemplo 123"] for offer in offers)
    assert all("merchant_locations" not in offer.eligibility.unknown_fields for offer in offers)
    assert any(evidence.method == "pdf-table" and evidence.page == 1 for evidence in offers[0].evidence)


def atlas_terms_source(document=None, expired=False):
    url = "https://www.bancoatlas.com.py/web/legal.pdf"
    html = '<div class="benefit-card" data-nombre="Comercio" data-pct="20%" data-label-pct="de reintegro" data-desc="con tarjetas de crédito" data-dias="Todos los martes" data-terminos="Desde el 1 de octubre de 2026 hasta el 31 de octubre de 2026." data-boton-url="' + url + '" data-boton-texto="Bases y condiciones" data-expired="' + str(expired).lower() + '"></div>'
    source = ScrapedSource("html", ATLAS, text=html, documents=[document] if document else [])
    return source, url


def test_atlas_terms_preserve_all_pdf_pages_exclusions_without_creating_shops():
    source, url = atlas_terms_source()
    source.documents = [ScrapedSource("pdf", url, text="BASES Y CONDICIONES\nDesde el 1 de octubre de 2026 hasta el 31 de octubre de 2026.\nEXCLUSIONES\nSe excluyen tarjetas corporativas y compras de delivery.", metadata={"role": "terms"})]
    offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert offer.publication == "confirmed"
    assert "EXCLUSIONES" in "\n".join(offer.terms)
    assert any(evidence.source_url == url and evidence.page == 1 and evidence.field == "terms" for evidence in offer.evidence)
    assert offer.eligibility.locations == []
    assert "merchant_locations" not in offer.eligibility.unknown_fields


def test_atlas_terms_table_percentages_and_names_are_not_global_benefits_or_shops():
    source, url = atlas_terms_source(ScrapedSource("pdf", "https://www.bancoatlas.com.py/web/legal.pdf", text="fixture", metadata={"role": "terms"}))
    pages = [PdfPage(1, "BASES Y CONDICIONES\nTarjeta Oro: 10% de descuento. Tarjeta Black: 30% de descuento.", [[['Nombre', 'Beneficio'], ['Rosa', '50% de descuento'], ['Margarita', '60% de descuento']]])]
    with patch("app.scraping.parsers.atlas_html_parser.read_pdf", return_value=pages):
        offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert [(benefit.type, benefit.percentage) for benefit in offer.benefits] == [("cashback", 20)]
    assert offer.eligibility.locations == []
    assert any(evidence.source_url == url and "Tarjeta Black" in evidence.text for evidence in offer.evidence)


def test_atlas_unreadable_or_scanned_terms_cannot_confirm_summary():
    for document, warning in [(ScrapedSource("pdf", "https://www.bancoatlas.com.py/web/legal.pdf", content=b"not a PDF", metadata={"role": "terms"}), "terms_unreadable"),
                              (ScrapedSource("pdf", "https://www.bancoatlas.com.py/web/legal.pdf", text="", metadata={"role": "terms"}), "terms_requires_ocr")]:
        source, _ = atlas_terms_source(document)
        promotion = AtlasHtmlParser().parse(source)[0]
        assert promotion.offers[0].publication == "pending"
        assert "terms_document" in promotion.offers[0].eligibility.unknown_fields
        assert source.metadata["parse_warning"] == warning
        assert promotion.metadata["offers_complete"] == "false"


def test_atlas_missing_terms_document_remains_pending():
    source, _ = atlas_terms_source()
    source.metadata["document_error"] = "download_failed"
    offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert offer.publication == "pending"
    assert "terms_document" in offer.eligibility.unknown_fields


def test_atlas_conflicting_pdf_validity_is_pending_and_keeps_both_sources():
    source, url = atlas_terms_source()
    source.documents = [ScrapedSource("pdf", url, text="BASES Y CONDICIONES\nDesde el 1 de octubre de 2026 hasta el 30 de noviembre de 2026.\nSe excluyen tarjetas corporativas.", metadata={"role": "terms"})]
    offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert (offer.validity_state, offer.publication) == ("conflict", "pending")
    assert source.metadata["parse_warning"] == "atlas_html_pdf_conflict"
    assert any(evidence.source_url == url and evidence.field == "validity" for evidence in offer.evidence)
    assert any(evidence.source_url == ATLAS for evidence in offer.evidence)


def test_atlas_expired_campaign_with_unreadable_terms_stays_retired():
    source, _ = atlas_terms_source(ScrapedSource("pdf", "https://www.bancoatlas.com.py/web/legal.pdf", text="", metadata={"role": "terms"}), expired=True)
    offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert offer.publication == "retired"
    assert "terms_document" in offer.eligibility.unknown_fields


def test_atlas_partially_scanned_terms_keeps_readable_pages_but_needs_review():
    source, url = atlas_terms_source(ScrapedSource("pdf", "https://www.bancoatlas.com.py/web/legal.pdf", text="fixture", metadata={"role": "terms"}))
    pages = [PdfPage(1, "BASES Y CONDICIONES\nDesde el 1 de octubre de 2026 hasta el 31 de octubre de 2026."), PdfPage(2, "")]
    with patch("app.scraping.parsers.atlas_html_parser.read_pdf", return_value=pages):
        offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert offer.publication == "pending"
    assert source.metadata["parse_warning"] == "terms_requires_ocr"
    assert "terms_document" in offer.eligibility.unknown_fields
    assert any(evidence.source_url == url and evidence.page == 1 for evidence in offer.evidence)


def dated_event_source(name: str):
    fixture = json.loads(parser_fixture("atlas_dated_events.json"))
    value = next(card for card in fixture["cards"] if card["name"] == name)
    return ScrapedSource("html", value["source_url"], text=value["html"], metadata=value["metadata"].copy())


@pytest.mark.parametrize("merchant,first,last", [
    ("Feria de Viajes", date(2026, 10, 23), date(2026, 10, 23)),
    ("delSol Shopping - Cheques delSol", date(2026, 10, 17), date(2026, 10, 17)),
    ("Cecconello", date(2026, 10, 15), date(2026, 10, 31)),
    ("Pre Venta Iphone 18", date(2026, 9, 25), date(2026, 10, 10)),
    ("Mariscal - Días M", date(2026, 10, 30), date(2026, 11, 1)),
])
def test_official_atlas_events_apply_only_on_explicit_days_inside_catalogue_window(merchant, first, last):
    source = dated_event_source(merchant)
    structured = json.loads(source.metadata["jsonld"])[0]
    offers = AtlasHtmlParser().parse(source)[0].offers
    expected = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    assert all(offer.schedule.kind == "specific_dates" and offer.schedule.dates == expected for offer in offers)
    assert all(offer.publication == "confirmed" for offer in offers)
    assert all((offer.valid_from, offer.valid_until) == (date.fromisoformat(structured["validFrom"]), date.fromisoformat(structured["validThrough"])) for offer in offers)
    assert all(any(evidence.method == "jsonld" for evidence in offer.schedule.evidence) for offer in offers)


def test_atlas_dated_event_requires_official_year_not_current_year():
    source = dated_event_source("Cecconello")
    source.metadata["jsonld"] = "[]"
    offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert offer.schedule.state == "unknown" and offer.publication == "pending"
    assert offer.valid_from is None and offer.valid_until is None


def test_atlas_dated_event_outside_official_window_stays_in_conflict():
    source = dated_event_source("Cecconello")
    rows = json.loads(source.metadata["jsonld"])
    rows[0]["validThrough"] = "2026-10-14"
    source.metadata["jsonld"] = json.dumps(rows)
    offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert (offer.schedule.state, offer.validity_state, offer.publication) == ("conflict", "conflict", "pending")


def test_atlas_dated_event_ambiguous_year_stays_pending():
    source = dated_event_source("Cecconello")
    rows = json.loads(source.metadata["jsonld"])
    rows[0].update(validFrom="2026-10-01", validThrough="2027-10-31")
    source.metadata["jsonld"] = json.dumps(rows)
    offer = AtlasHtmlParser().parse(source)[0].offers[0]
    assert offer.schedule.state == "unknown" and offer.publication == "pending"
