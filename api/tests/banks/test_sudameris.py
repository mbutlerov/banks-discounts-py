"""Sudameris source, parser, document and catalogue regressions."""
from __future__ import annotations

from datetime import date
import json
import unittest
from unittest.mock import patch

import pytest

from app.scraping.clients.http_client import HttpClientError
from app.scraping.parsers.pdf_offers import PdfPage, parse_sudameris_tables, parse_sudameris_text
from app.scraping.parsers.sudameris_html_parser import SudamerisHtmlParser
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.bank_documents import attach_bank_documents
from app.scraping.sources.helpers import SourceDiscoveryError
from app.scraping.sources.sudameris_html_source import SudamerisHtmlSource, _find_pdf_url
from tests.helpers.sources import FakeHttp, parser_fixture, pdf_fixture

SUDAMERIS = "https://www.sudameris.com.py/beneficios/destacado/875/detalle"


class SourceContractTests(unittest.TestCase):
    def test_sudameris_discovers_both_namespaces(self):
        base = "https://www.sudameris.com.py"
        http = FakeHttp({base + "/beneficios/": '<a href="/beneficios/destacado/1/detalle">A</a>',
                         base + "/beneficios/promociones": '<a href="/beneficios/promocion/1/detalle">B</a>',
                         base + "/beneficios/destacado/1/detalle": "<h3>A</h3>",
                         base + "/beneficios/promocion/1/detalle": "<h3>B</h3>"})
        self.assertEqual([s.metadata["promo_id"] for s in SudamerisHtmlSource(http).fetch()], ["destacado:1", "promocion:1"])


class OfficialParserTests(unittest.TestCase):
    def test_sudameris_ordinal_and_month_day_html(self):
        plub = SudamerisHtmlParser().parse(ScrapedSource("html", "https://www.sudameris.com.py/beneficios/destacado/360/detalle", text=parser_fixture("sudameris_plub.html")))[0]
        self.assertEqual(plub.offers[0].schedule.ordinal, -1)
        self.assertEqual(plub.offers[0].valid_from, date(2026, 8, 1))
        self.assertEqual(plub.offers[0].valid_until, date(2026, 10, 31))
        biggie = SudamerisHtmlParser().parse(ScrapedSource("html", "https://www.sudameris.com.py/beneficios/destacado/891/detalle", text=parser_fixture("sudameris_biggie.html")))[0]
        self.assertEqual(biggie.offers[0].schedule.month_days, [1])

    def test_sudameris_rows_have_individual_validity_and_black_extra(self):
        pages, url = pdf_fixture("sudameris_gastronomy")
        offers = parse_sudameris_tables(pages, url)
        kaiseiki = [o for o in offers if o.merchant_name == "KAISEKI"]
        patio = next(o for o in offers if o.merchant_name == "PATIO COLONIAL")
        self.assertEqual([o.benefits[0].percentage for o in kaiseiki], [20, 25])
        self.assertEqual(kaiseiki[1].eligibility.cards, ["Mastercard Black", "Visa Infinite"])
        self.assertEqual(kaiseiki[0].schedule.weekdays, [1, 2, 3, 4, 5])
        self.assertEqual(patio.schedule.weekdays, [4, 5, 6, 7])
        self.assertEqual(patio.valid_until, date(2026, 8, 2))
        self.assertEqual(patio.caps[0].type, "purchase")
        self.assertEqual(patio.caps[0].period, "month")
        self.assertEqual(kaiseiki[0].evidence[0].page, 2)

    def test_sudameris_discount_cashback_and_installments_coexist(self):
        value = json.loads(parser_fixture("sudameris_farmacenter.json"))
        offers = parse_sudameris_text(value["text"], value["source_url"], "Farmacenter")
        self.assertEqual(len(offers), 1)
        self.assertEqual([b.type for b in offers[0].benefits], ["discount", "cashback", "installments"])
        self.assertEqual([b.percentage for b in offers[0].benefits[:2]], [20, 10])
        self.assertEqual(offers[0].schedule.ordinal, 1)
        self.assertEqual(offers[0].schedule.weekdays, [3])
        self.assertEqual(offers[0].publication, "confirmed")

    def test_sudameris_card_tiers_and_accommodation_only_installments(self):
        value = json.loads(parser_fixture("sudameris_tatano.json"))
        offers = parse_sudameris_text(value["text"], value["source_url"], "Tatano")
        self.assertEqual(len(offers), 4)
        basic, basic_lodging, premium, premium_lodging = offers
        self.assertEqual(basic.benefits[0].percentage, 20)
        self.assertEqual(premium.benefits[0].percentage, 25)
        self.assertEqual(premium.eligibility.cards, ["Mastercard Black", "Visa Infinite"])
        self.assertEqual(len(basic.benefits), 1)
        self.assertEqual(basic_lodging.benefits[1].installments, 12)
        self.assertTrue(any("alojamiento" in c for c in premium_lodging.benefits[1].conditions))
        self.assertEqual(len({o.key for o in offers}), 4)


def luisito_source(**metadata):
    value = json.loads(parser_fixture("sudameris_luisito.json"))
    return ScrapedSource("html", SUDAMERIS, text=parser_fixture("sudameris_luisito.html"),
                         metadata={"promo_id": "destacado:875", **metadata},
                         documents=[ScrapedSource("pdf", value["source_url"], text=value["pages"][0]["text"])])


def test_terms_button_preserves_relative_paths_queries_and_deduplicates():
    base = "https://www.sudameris.com.py/beneficios/destacado/875/detalle"
    pdf = "https://www.sudameris.com.py/storage/legal.PDF?download=1"
    html = '<a href="/storage/legal.PDF?download=1#page=1">Bases y condiciones</a><a href="/storage/legal.PDF?download=1">Bases y condiciones</a>'
    client = FakeHttp({pdf: b"%PDF-1.7\nfixture"})
    source = ScrapedSource("html", base, text=html)
    attach_bank_documents(source, client, "sudameris.com.py")
    assert _find_pdf_url(html, base) == pdf
    assert client.urls == [pdf]
    assert source.documents[0].metadata["role"] == "terms"
    assert source.metadata["pdf_url"] == pdf


def test_sudameris_single_failed_detail_makes_discovery_partial():
    base = "https://www.sudameris.com.py"
    listing = '<a href="/beneficios/destacado/1/detalle">One</a><a href="/beneficios/destacado/2/detalle">Two</a>'
    client = FakeHttp({base + "/beneficios/": listing, base + "/beneficios/promociones": listing,
                       base + "/beneficios/destacado/1/detalle": "<h3>One</h3>",
                       base + "/beneficios/destacado/2/detalle": HttpClientError("HTTP 503")})
    sources = SudamerisHtmlSource(client).fetch()
    assert len(sources) == 1
    assert sources[0].metadata["discovery_warning"] == "detail_download_failed:1/2"


def test_sudameris_no_downloaded_details_is_a_failure():
    base = "https://www.sudameris.com.py"
    listing = '<a href="/beneficios/destacado/1/detalle">One</a>'
    client = FakeHttp({base + "/beneficios/": listing, base + "/beneficios/promociones": listing,
                       base + "/beneficios/destacado/1/detalle": HttpClientError("HTTP 503")})
    with pytest.raises(SourceDiscoveryError):
        SudamerisHtmlSource(client).fetch()


def test_luisito_pdf_keeps_actual_schedule_cap_exclusions_and_page_evidence():
    promotion = SudamerisHtmlParser().parse(luisito_source())[0]
    offer = promotion.offers[0]
    assert offer.publication == "confirmed"
    assert offer.schedule.weekdays == [2]
    assert (offer.valid_from, offer.valid_until) == (date(2026, 5, 26), date(2026, 11, 30))
    assert (offer.benefits[0].type, offer.benefits[0].percentage) == ("cashback", 20)
    assert (offer.caps[0].type, offer.caps[0].amount, offer.caps[0].period, offer.caps[0].scope) == ("purchase", 1000000, "month", "account")
    assert offer.eligibility.card_types == ["credit"]
    assert "EXCLUSIONES" in "\n".join(offer.terms)
    assert all(evidence.page == 1 for evidence in offer.evidence)
    assert offer.schedule.evidence[0].page == 1
    assert any(evidence.field == "eligibility" and "Bancard Check" in evidence.text for evidence in offer.evidence)


def test_disagreement_between_luisito_page_and_pdf_remains_pending():
    source = luisito_source()
    source.text = source.text.replace("30 de noviembre 2026", "31 de diciembre 2026")
    offer = SudamerisHtmlParser().parse(source)[0].offers[0]
    assert (offer.validity_state, offer.publication) == ("conflict", "pending")
    assert source.metadata["parse_warning"] == "html_pdf_conflict"
    assert any(evidence.source_url == SUDAMERIS for evidence in offer.evidence)


def test_failed_terms_pdf_does_not_confirm_luisito_summary():
    source = luisito_source(document_error="download_failed")
    source.documents = []
    offer = SudamerisHtmlParser().parse(source)[0].offers[0]
    assert offer.publication == "pending"
    assert "terms_document" in offer.eligibility.unknown_fields


def test_official_regional_seven_columns_keep_discount_cashback_installments_and_card_extra():
    pages, url = pdf_fixture("sudameris_zona_central_columns")
    offers = parse_sudameris_tables(pages, url)
    materassi = next(offer for offer in offers if offer.merchant_name == "MATERASSI COLCHONES")
    assert [(benefit.type, benefit.percentage, benefit.installments) for benefit in materassi.benefits] == [
        ("discount", 30, None), ("installments", None, 12), ("cashback", 10, None),
    ]
    assert (materassi.valid_from, materassi.valid_until) == (date(2026, 3, 4), date(2026, 8, 1))
    assert materassi.caps[0].amount == 10000000 and materassi.caps[0].type == "purchase"
    assert materassi.schedule.weekdays == [3, 4, 5, 6]
    diana = [offer for offer in offers if offer.merchant_name == "DIANA APODACA"]
    assert [offer.benefits[0].percentage for offer in diana] == [20, 25]
    assert all(any(benefit.installments == 12 for benefit in offer.benefits) for offer in diana)
    assert diana[1].eligibility.cards == ["Mastercard Black", "Visa Infinite"]
    assert all(offer.publication == "confirmed" and offer.evidence[0].page == 2 for offer in offers)


def test_official_regional_fifteen_columns_keep_base_and_unresolved_elite_separate():
    pages, url = pdf_fixture("sudameris_zona_este_columns")
    offers = parse_sudameris_tables(pages, url)
    normal = next(offer for offer in offers if offer.merchant_name == "360 Consultores S.A.")
    assert normal.publication == "confirmed" and normal.schedule.weekdays == [5, 6]
    assert (normal.valid_from, normal.valid_until) == (date(2026, 3, 6), date(2027, 2, 27))
    poly = [offer for offer in offers if offer.merchant_name == "Poly Shop"]
    assert [offer.benefits[0].percentage for offer in poly] == [20, 25]
    assert [offer.publication for offer in poly] == ["confirmed", "pending"]
    assert "cards" in poly[1].eligibility.unknown_fields and poly[1].eligibility.cards == []
    assert all(any(benefit.installments == 10 for benefit in offer.benefits) for offer in poly)
    assert all(offer.caps[0].amount == 800000 for offer in poly)


def test_regional_unknown_spacer_data_cannot_be_silently_discarded():
    pages, url = pdf_fixture("sudameris_zona_este_columns")
    page = pages[1]
    page.tables[0][1][1] = "Una condición que cambió el formato"
    assert parse_sudameris_tables(pages, url) == []


def parsed_sudameris_fixture(name, title):
    pages, url = pdf_fixture(name)
    source = ScrapedSource("html", SUDAMERIS, text="<h3>" + title + "</h3>",
                           documents=[ScrapedSource("pdf", url, text="fixture")])
    with patch("app.scraping.parsers.sudameris_html_parser.read_pdf", return_value=pages):
        promotions = SudamerisHtmlParser().parse(source)
    return source, promotions


def test_official_enex_annexes_bind_physical_and_app_stations_to_their_own_variant():
    source, promotions = parsed_sudameris_fixture("sudameris_enex_annex", "ENEX Y APP MI ENEX")
    physical, app = promotions[0].offers
    assert [offer.benefits[0].percentage for offer in (physical, app)] == [10, 20]
    assert all(offer.publication == "confirmed" for offer in (physical, app))
    assert all((offer.schedule.ordinal, offer.schedule.weekdays) == (1, [1]) for offer in (physical, app))
    assert all(len(offer.eligibility.locations) == 2 for offer in (physical, app))
    assert all(len(offer.locations) == 2 and offer.location_scope == "specified" for offer in (physical, app))
    assert physical.locations[0].city == "Luque" and physical.locations[0].address == "Ybyturusu esq. Abdón Caballero"
    assert app.locations[0].channels == ["app"] and app.locations[0].evidence[0].page == 3
    assert all(location.startswith("ENEX ") for location in physical.eligibility.locations)
    assert all(location.startswith("MI APP ENEX ") for location in app.eligibility.locations)
    assert physical.eligibility.cities == ["Luque"]
    assert app.eligibility.cities == ["Asunción"] and app.eligibility.channels == ["app"]
    assert physical.caps[0].amount == 600000
    assert {evidence.page for evidence in physical.evidence if evidence.field == "merchant_locations"} == {2}
    assert {evidence.page for evidence in app.evidence if evidence.field == "merchant_locations"} == {3}
    assert promotions[0].metadata["offers_complete"] == "true"
    assert "parse_warning" not in source.metadata


def test_official_ccu_address_annex_does_not_block_one_merchant_legal_conditions():
    _, promotions = parsed_sudameris_fixture("sudameris_ccu_annex", "Estaciones de Servicio CCU")
    offer = promotions[0].offers[0]
    assert offer.publication == "confirmed" and offer.schedule.ordinal == 1 and offer.schedule.weekdays == [4]
    assert offer.benefits[0].percentage == 20 and offer.caps[0].amount == 1000000
    assert len(offer.eligibility.locations) == 2 and offer.eligibility.cities == ["OBLIGADO", "SANTA RITA"]
    assert all(evidence.page == 2 for evidence in offer.evidence if evidence.field == "merchant_locations")


def test_unknown_annex_columns_do_not_confirm_or_invent_a_station_list():
    pages, url = pdf_fixture("sudameris_ccu_annex")
    pages[1].tables[0][0][4] = "Otro campo"
    source = ScrapedSource("html", SUDAMERIS, text="<h3>Estaciones de Servicio CCU</h3>",
                           documents=[ScrapedSource("pdf", url, text="fixture")])
    with patch("app.scraping.parsers.sudameris_html_parser.read_pdf", return_value=pages):
        offer = SudamerisHtmlParser().parse(source)[0].offers[0]
    assert offer.publication == "pending" and offer.eligibility.locations == []
    assert source.metadata["parse_warning"] == "unrecognized_offer_variants"


def test_official_membership_terms_produce_three_product_variants_without_mixing_installments():
    _, promotions = parsed_sudameris_fixture("sudameris_live_fitness", "LIVE FITNESS")
    offers = promotions[0].offers
    assert len(offers) == len({offer.key for offer in offers}) == 3
    assert [offer.benefits[0].percentage for offer in offers] == [10, 15, 20]
    assert [offer.benefits[1].installments for offer in offers] == [3, 6, 12]
    assert all(offer.publication == "confirmed" and offer.schedule.kind == "all_days" for offer in offers)
    assert [offer.benefits[0].conditions[0] for offer in offers] == ["Membresía trimestral", "Membresía semestral", "Membresía anual"]


def test_unlabelled_same_type_percentages_still_require_review():
    text = "VIGENCIA\nTodos los días.\nDesde el 1 de octubre de 2026 hasta el 31 de diciembre de 2026.\nBENEFICIOS\n• 10% de descuento.\n• 20% de descuento.\nCONDICIONES\nAplica con tarjeta de crédito MasterCard y Visa."
    offers = parse_sudameris_text(text, SUDAMERIS, "Comercio")
    assert len(offers) == 2 and all(offer.publication == "pending" for offer in offers)


def test_conflicting_duplicate_merchant_rows_remain_distinct_pending_variants():
    pages, url = pdf_fixture("sudameris_zona_este_columns")
    row = pages[1].tables[0][1].copy()
    row[6] = "20% de reintegro\nHasta 10 cuotas sin interés"
    pages[1].tables[0].append(row)
    source = ScrapedSource("html", SUDAMERIS, text="<h3>ZONA ESTE</h3>",
                           documents=[ScrapedSource("pdf", url, text="fixture")])
    with patch("app.scraping.parsers.sudameris_html_parser.read_pdf", return_value=pages):
        promotions = SudamerisHtmlParser().parse(source)
    variants = next(promotion.offers for promotion in promotions if promotion.merchant_name == "360 Consultores S.A.")
    assert len(variants) == len({offer.key for offer in variants}) == 2
    assert all(offer.publication == "pending" and "offer_variant" in offer.eligibility.unknown_fields for offer in variants)
    assert source.metadata["parse_warning"] == "duplicate_merchant_variants"
    assert all(promotion.metadata["offers_complete"] == "false" for promotion in promotions)
