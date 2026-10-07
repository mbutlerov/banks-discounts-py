"""Ueno source, parser, document and catalogue regressions."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from io import BytesIO
import unittest

import pytest
from pypdf import PdfWriter
from pypdf.annotations import Link

from app.scraping.clients.http_client import HttpClientError
from app.scraping.parsers.pdf_offers import PdfPage, parse_ueno_tables
from app.scraping.parsers.ueno_html_parser import UenoHtmlParser
from app.scraping.parsers.ueno_legal_tables import parse_ueno_legal_tables
from app.scraping.parsers.ueno_pdf_details_parser import parse_pdf_details
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.helpers import SourceDiscoveryError
from app.scraping.sources.ueno_catalog import (
    ALLIANCES_URL, catalogue_details, catalogue_pdf_urls, official_ueno_url,
)
from app.scraping.sources.ueno_html_source import LEGAL_URL, UenoHtmlSource
from tests.helpers.sources import FakeHttp, alliance_fixture, pdf_fixture


class OfficialParserTests(unittest.TestCase):
    def test_ueno_power_inherits_only_group_cells_with_merchant_days(self):
        pages, url = pdf_fixture("ueno_power")
        offers = parse_ueno_tables(pages, url)
        ark = [o for o in offers if o.merchant_name == "ARK CAFÉ"]
        canela = [o for o in offers if o.merchant_name == "CANELA CAFETERÍA"]
        billy = [o for o in offers if o.merchant_name == "BILLY SMASH"]
        self.assertEqual(len(ark), 2)
        self.assertEqual([o.benefits[0].percentage for o in ark], [10, 50])
        self.assertFalse(ark[0].eligibility.personalization_required)
        self.assertTrue(ark[1].eligibility.personalization_required)
        self.assertEqual(ark[0].schedule.dates, [date(2026, 10, d) for d in [5, 12, 19, 26]])
        self.assertEqual(canela[0].schedule.dates, ark[0].schedule.dates)
        self.assertEqual(billy[0].schedule.dates, [date(2026, 10, d) for d in [6, 13, 20, 27]])
        self.assertEqual([c.period for c in ark[0].caps], ["week", "month"])
        self.assertEqual(ark[0].eligibility.processors, ["upay"])

    def test_ueno_fuel_keeps_caps_together_with_the_same_level(self):
        pages, url = pdf_fixture("ueno_fuel")
        offers = parse_ueno_tables(pages, url)
        copetrol = [o for o in offers if o.merchant_name == "COPETROL"]
        self.assertEqual([o.benefits[0].percentage for o in copetrol], [40, 30])
        self.assertEqual([c.amount for c in copetrol[0].caps], [150000, 60000])
        self.assertEqual([c.amount for c in copetrol[1].caps], [125000, 37500])
        self.assertEqual(copetrol[0].eligibility.levels, ["Nivel 5"])
        self.assertIn("anexo", " ".join(copetrol[0].eligibility.conditions))

    def test_ueno_html_does_not_fetch_in_parser_or_infer_url_month(self):
        source = ScrapedSource("html", "https://www.ueno.com.py/beneficio-byc/oct2026/combustibles/",
                               text="<h1>Beneficio de Reintegro COMBUSTIBLES | OCT 2026</h1>")
        http = FakeHttp({})
        promo = UenoHtmlParser(http_client=http).parse(source)[0]
        self.assertEqual(http.urls, [])
        self.assertIsNone(promo.start_date)
        self.assertEqual(promo.offers[0].validity_state, "unknown")
        self.assertEqual(promo.offers[0].publication, "pending")

    def test_legacy_pdf_summary_never_uses_max_tier(self):
        details = parse_pdf_details("nivel 5 40% Gs. 150.000\nnivel 4 30% Gs. 125.000")
        self.assertIsNone(details.discount_percentage)
        self.assertEqual(details.raw_text, "nivel 5 40% Gs. 150.000\nnivel 4 30% Gs. 125.000")

    def test_targeted_coupon_separates_optional_credit_installments(self):
        pages, url = pdf_fixture("ueno_coupon")
        offers = parse_ueno_tables(pages, url)
        self.assertEqual(len(offers), 2)
        self.assertEqual(offers[0].merchant_name, "CECOTEC")
        self.assertEqual(offers[0].benefits[0].percentage, 15)
        self.assertEqual(offers[0].eligibility.card_types, ["credit", "debit"])
        self.assertEqual(offers[1].eligibility.card_types, ["credit"])
        self.assertEqual(offers[1].benefits[1].installments, 12)
        self.assertTrue(all(o.eligibility.personalization_required for o in offers))
        self.assertTrue(all(o.valid_until == date(2026, 10, 31) for o in offers))

    def test_fixed_amount_coupon_never_becomes_a_percentage(self):
        pages, url = pdf_fixture("ueno_coupon_fixed")
        offer = parse_ueno_tables(pages, url)[0]
        self.assertIsNone(offer.benefits[0].percentage)
        self.assertIn("50.000", offer.benefits[0].label)
        self.assertEqual(offer.caps[0].amount, 50000)
        self.assertEqual(offer.caps[0].type, "cashback")
        self.assertTrue(any("300.000" in c for c in offer.eligibility.conditions))

    def test_targeted_combo_has_each_benefits_actual_start_and_cap(self):
        pages, url = pdf_fixture("ueno_combo")
        offers = parse_ueno_tables(pages, url)
        self.assertEqual([o.merchant_name for o in offers], ["Pago De Servicios", "Bolt"])
        self.assertTrue(all(o.valid_from == date(2026, 10, 5) for o in offers))
        self.assertTrue(all(o.eligibility.personalization_required for o in offers))
        self.assertEqual(offers[1].caps[1].amount, 30000)


CATALOGUE_URL = "https://www.ueno.com.py/wp-content/uploads/2026/10/BENEFICIOS-ueno-octubre2026.pdf"


def parsed(name):
    value = alliance_fixture(name)
    return parse_ueno_legal_tables([PdfPage(**page) for page in value["pages"]], value["source_url"], name)


def navigation_pdf(links_by_page):
    writer = PdfWriter()
    for number, urls in enumerate(links_by_page):
        writer.add_blank_page(width=320, height=500)
        for index, url in enumerate(urls):
            writer.add_annotation(page_number=number, annotation=Link(rect=(10, 10 + index * 15, 200, 20 + index * 15), url=url))
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_catalogue_scans_all_embeds_and_ignores_external_pdf():
    html = '<iframe src="https://www.googletagmanager.com/ns.html"></iframe>'
    html += f'<iframe src="{CATALOGUE_URL}?download=1#page=1"></iframe>'
    html += '<a href="https://advertiser.example/offer.pdf">Descargar pdf</a>'
    assert catalogue_pdf_urls(html) == [CATALOGUE_URL + "?download=1"]


def test_all_real_catalogue_annotations_are_discovered_without_thirty_page_limit():
    value = alliance_fixture("catalogue")
    pages = [[] for _ in range(value["page_count"])]
    for link in value["links"]:
        pages[link["page"] - 1].append(link["url"])
    # A future longer catalogue must include its final page as well.
    pages.extend([[], [], [], [], [], ["https://www.ueno.com.py/beneficio-byc/nov2026/final/"]])
    details, page_count = catalogue_details(navigation_pdf(pages), CATALOGUE_URL)
    assert page_count == 31
    assert len(details) == 28  # 27 actual legal targets + the final synthetic target
    entertainment = next(d for d in details if d.url.endswith("/entretenimiento/"))
    assert entertainment.pages == [8]
    assert details[-1].pages == [31]
    assert all("ticketea" not in d.url for d in details)


@pytest.mark.parametrize("url", ["https://ueno.com.py.evil.example/beneficio-byc/x/", "https://user:secret@www.ueno.com.py/a/", "file:///a.pdf", "https://www.ueno.com.py:8443/a.pdf"])
def test_catalogue_links_only_use_official_hosts(url):
    assert official_ueno_url(url, ALLIANCES_URL) is None


def test_primary_catalogue_precedes_hidden_legal_complement_and_deduplicates():
    primary = "https://www.ueno.com.py/beneficio-byc/sep2026/ubox/"
    extra = "https://www.ueno.com.py/beneficio-byc/oct2026/gastronomia/"
    body = navigation_pdf([[primary]])
    http = FakeHttp({ALLIANCES_URL: f'<a href="{CATALOGUE_URL}">Descargar pdf</a>', CATALOGUE_URL: body,
                     LEGAL_URL: f'<div style="display:none"><a href="{primary}">Ver</a><a href="{extra}">Ver</a></div>',
                     primary: "<h1>ubox</h1>", extra: "<h1>Gastronomía</h1>"})
    sources = UenoHtmlSource(http).fetch()
    assert [s.source_url for s in sources] == [primary, extra]
    assert sources[0].metadata["catalogue_role"] == "monthly-primary"
    assert sources[0].metadata["also_in_legal_index"] == "true"
    assert sources[0].metadata["catalogue_pages"] == "1"
    assert sources[1].metadata["catalogue_role"] == "legal-complement"
    assert http.urls.count(primary) == 1


def test_unavailable_primary_is_explicit_partial_legal_fallback():
    detail = "https://www.ueno.com.py/beneficio-byc/oct2026/optica-luce/"
    http = FakeHttp({ALLIANCES_URL: HttpClientError("HTTP 503"), LEGAL_URL: f'<a href="{detail}">Ver</a>', detail: "<h1>Óptica Luce</h1>"})
    sources = UenoHtmlSource(http).fetch()
    assert "mensual" in sources[0].metadata["discovery_warning"]
    assert sources[0].metadata["catalogue_role"] == "legal-complement"


def test_discovery_failure_never_means_successful_empty_catalogue():
    http = FakeHttp({ALLIANCES_URL: "<h1>Changed page</h1>", LEGAL_URL: "<h1>Changed page</h1>"})
    with pytest.raises(SourceDiscoveryError):
        UenoHtmlSource(http).fetch()


def test_supermarket_annex_joins_each_level_to_its_merchant_and_purchase_minimum():
    offers = parsed("supermercados")
    merchant = next(o.merchant_name for o in offers)
    tiers = [o for o in offers if o.merchant_name == merchant]
    assert len(tiers) == 5
    assert [o.benefits[0].percentage for o in tiers] == [40, 30, 25, 15, 10]
    assert tiers[0].eligibility.levels == ["Nivel 5"]
    assert [c.amount for c in tiers[0].caps] == [500000, 200000]
    assert [c.type for c in tiers[0].caps] == ["purchase", "cashback"]
    assert all(c.period == "campaign" and c.scope == "customer" for c in tiers[0].caps)
    assert all(o.publication == "confirmed" and o.schedule.kind == "all_days" for o in tiers)
    assert any("300.000" in c for c in tiers[0].eligibility.conditions)
    assert tiers[0].evidence[1].page == 3
    assert tiers[0].eligibility.processors == ["upay"]
    assert "QR" not in tiers[0].eligibility.channels


def test_palmear_saturday_is_not_every_day():
    offers = parsed("feria-palmear")
    assert len(offers) == 5
    assert all(o.schedule.weekdays == [6] and o.publication == "confirmed" for o in offers)


def test_pharmacy_second_annex_has_branch_addresses_and_actual_processors():
    offers = parsed("farmacias")
    branches = [o for o in offers if o.merchant_name.startswith("MAXIFARMA") and o.eligibility.locations]
    assert branches
    assert all(o.eligibility.processors == ["upay"] for o in branches)
    assert all(len(o.eligibility.locations) == 1 for o in branches)
    assert any("Mcal." in o.eligibility.locations[0] for o in branches)
    assert all(o.valid_from == date(2026, 10, 1) and o.valid_until == date(2026, 10, 31) for o in branches)
    assert all(o.publication == "confirmed" for o in branches)


def test_jewellers_are_named_separately_and_no_cap_means_no_numeric_limit():
    offers = parsed("joyerias")
    assert {o.merchant_name for o in offers} == {"Neusa Joyería", "Oscar Joyas"}
    assert all(o.caps == [] for o in offers)
    assert sum(any(b.type == "installments" for b in o.benefits) for o in offers) == 10


def test_koala_keeps_discount_and_cashback_on_net_purchase_separate():
    offers = parsed("koala")
    benefits = offers[0].benefits
    assert [(b.type, b.percentage) for b in benefits] == [("discount", Decimal(45)), ("cashback", Decimal(10))]
    assert any("neto" in c for c in benefits[1].conditions)
    assert all(b.percentage != 55 for o in offers for b in o.benefits)


def test_separate_entertainment_annexes_never_cross_apply_benefits():
    offers = parsed("entretenimiento")
    flight = [o for o in offers if "FLIGHTNEX" in o.merchant_name]
    assert len(flight) == 5
    assert all([b.type for b in o.benefits] == ["cashback"] for o in flight)
    gorillaz = next(o for o in offers if o.merchant_name == "GORILLAZ")
    assert [b.type for b in gorillaz.benefits] == ["installments"]
    assert gorillaz.benefits[0].installments == 12
    assert gorillaz.eligibility.channels == ["Sitio web Ticketea"]
    camilo = next(o for o in offers if o.merchant_name == "CAMILO")
    assert camilo.publication == "pending"
    assert "presale_start_date" in camilo.eligibility.unknown_fields


def test_all_135_installment_merchants_use_their_legal_pages_and_annex():
    offers = parsed("cuotas-sin-intereses")
    assert len(offers) == 135
    assert len({o.merchant_name for o in offers}) == 135
    assert all([b.type for b in o.benefits] == ["installments"] for o in offers)
    assert all(o.benefits[0].installments == 12 and o.benefits[0].is_maximum for o in offers)
    assert all(o.eligibility.card_types == ["credit"] for o in offers)
    assert all(o.eligibility.processors == ["upay"] for o in offers)
    assert all(o.publication == "confirmed" and o.schedule.kind == "all_days" for o in offers)
    assert all(o.valid_from == date(2026, 10, 1) and o.valid_until == date(2026, 10, 31) for o in offers)
    assert {o.evidence[1].page for o in offers} == {2, 3, 4, 5, 6}
    assert any("showroom de RRDE" in o.merchant_name for o in offers)


def test_club_uses_its_contractual_multiyear_validity_and_per_network_concepts():
    offers = parsed("clubes")
    assert len(offers) == 15
    assert all(o.valid_from == date(2025, 9, 1) and o.valid_until == date(2029, 8, 26) for o in offers)
    assert all(c.period == "month" for o in offers for c in o.caps)
    automatic = [o for o in offers if o.eligibility.processors == ["Infonet"]]
    assert len(automatic) == 5
    assert all(o.eligibility.channels == ["Débito automático"] for o in automatic)
    assert all([b.type for b in o.benefits] == ["cashback"] for o in automatic)


def test_ubox_uses_september_december_and_black_additional_total_cap():
    offers = parsed("ubox")
    assert [o.benefits[0].percentage for o in offers] == [20, 50]
    assert all(o.valid_from == date(2026, 9, 1) and o.valid_until == date(2026, 12, 31) for o in offers)
    assert offers[1].eligibility.cards == ["Mastercard Dúo Black", "Mastercard Dúo Ultra Black"]
    assert [c.amount for c in offers[1].caps] == [127000, 63500]
    assert all(c.period == "campaign" for o in offers for c in o.caps)
    assert len(offers[0].eligibility.locations) == 8


def test_annex_row_without_explicit_or_shared_contractual_year_is_pending():
    value = alliance_fixture("supermercados")
    pages = [PdfPage(**page) for page in value["pages"]]
    pages[0].text = pages[0].text.replace("del 2026", "")
    offers = parse_ueno_legal_tables(pages, value["source_url"], "Supermercados")
    assert all(o.publication == "pending" and o.validity_state == "unknown" for o in offers)
    assert all(o.valid_from is None and o.valid_until is None for o in offers)


def test_cap_period_is_never_used_as_purchase_calendar():
    value = alliance_fixture("supermercados")
    pages = [PdfPage(**page) for page in value["pages"]]
    pages[0].text = pages[0].text.replace("El beneficio se aplicará a cada cliente durante la vigencia de la promoción", "Los límites se aplicarán a cada cliente durante la vigencia de la promoción")
    offers = parse_ueno_legal_tables(pages, value["source_url"], "Supermercados")
    assert all(o.schedule.state == "unknown" and o.publication == "pending" for o in offers)


def test_petropar_branch_identity_includes_address_and_payment_channel():
    offers = parsed("petropar")
    assert any(o.eligibility.channels == ["App Petropar"] for o in offers)
    assert all(len(o.eligibility.locations) == 1 for o in offers)
    value = alliance_fixture("petropar")
    pages = [PdfPage(**page) for page in value["pages"]]
    duplicate = deepcopy(pages[1].tables[0][-1])
    pages[1].tables[0].append(duplicate)
    repeated = parse_ueno_legal_tables(pages, value["source_url"], "Petropar")
    assert {o.key for o in repeated} == {o.key for o in offers}


def test_current_petropar_annex_preserves_all_locations_and_exact_payment_channels():
    offers = parsed("petropar-current")
    locations = {location.key: location for offer in offers for location in offer.locations}
    assert len(locations) == 214
    assert len(offers) == 1070  # Five legal levels for every official annex row.
    assert sum(location.channels == ["POS"] for location in locations.values()) == 191
    assert sum(location.channels == ["App Petropar"] for location in locations.values()) == 23
    assert all(location.processors == [] for location in locations.values()
               if location.channels == ["App Petropar"])
    assert all(offer.eligibility.channels == offer.locations[0].channels for offer in offers)
    assert all(len(offer.locations) == 1 for offer in offers)
    assert all(location.address and location.city for location in locations.values())
    new_station = next(location for location in locations.values() if location.name == "PETROPAR PUNTO 63")
    assert new_station.city == "Piribebuy"
    assert new_station.address == "Ruta Py02 - km 63 - Piribebuy"
    assert new_station.processors == ["upay"]
    assert new_station.evidence[0].page == 11
    assert new_station.evidence[0].source_url == alliance_fixture("petropar-current")["source_url"]


def test_petropar_locations_do_not_merge_same_named_stations_or_multiply_for_levels():
    offers = parsed("petropar")
    luque = [offer for offer in offers if offer.merchant_name == "PETROPAR LUQUE"]
    locations = {location.key: location for offer in luque for location in offer.locations}
    assert len(luque) == 10
    assert len(locations) == 2
    assert {location.address for location in locations.values()} == {
        "AVDA. PUERTO PINASCO Y CHILE",
        "AVDA. ELIZARDO AQUINO ESQ. PEDRO MAYOR ECHAURI",
    }
    assert all(location.city == "LUQUE" for location in locations.values())
    assert all(location.evidence[0].page == 7 for location in locations.values())
    assert "0e3b28ac857062d52a67757f" in {offer.key for offer in luque}
    assert "4f4c033b00026cf76e1f401b" in {offer.key for offer in luque}


def test_petropar_same_address_keeps_pos_and_app_adherence_separate():
    offers = parsed("petropar-current")
    address = "AUTOPISTA SILVIO PETTIROSSI CASI TTE. CORONEL RAMOS"
    locations = {location.key: location for offer in offers for location in offer.locations
                 if location.address == address and location.city == "LUQUE"}
    assert len(locations) == 2
    assert {location.name for location in locations.values()} == {"PETROPAR LUQUE - FAP1", "PETROPAR APP LUQUE"}
    assert {tuple(location.channels) for location in locations.values()} == {("POS",), ("App Petropar",)}
    assert {location.evidence[0].page for location in locations.values()} == {7, 12}


def test_structured_branch_location_never_invents_city_from_address():
    offers = parsed("farmacias")
    branches = [offer for offer in offers if offer.merchant_name.startswith("MAXIFARMA") and offer.locations]
    assert branches
    assert all(location.city is None for offer in branches for location in offer.locations)
    assert all(offer.eligibility.cities == [] for offer in branches)
    assert all(location.address == offer.eligibility.locations[0] for offer in branches for location in offer.locations)


def test_incomplete_tier_is_pending_and_cannot_retire_previous_variants(monkeypatch):
    value = alliance_fixture("supermercados")
    pages = [PdfPage(**page) for page in value["pages"]]
    pages[0].tables[0][2][1] = "unreadable%"
    monkeypatch.setattr("app.scraping.parsers.ueno_html_parser.read_pdf", lambda document: pages)
    source = ScrapedSource("html", value["detail_url"], text="<h1>Beneficio SUPERMERCADOS | OCT 2026</h1>",
                           documents=[ScrapedSource("pdf", value["source_url"], text="fixture")])
    promotions = UenoHtmlParser().parse(source)
    assert source.metadata["parse_warning"] == "ueno_incomplete_legal_table"
    assert all(p.metadata["offers_complete"] == "false" for p in promotions)
    assert all(o.publication == "pending" for p in promotions for o in p.offers)


def test_missing_merchant_annex_never_confirms_the_whole_category():
    value = alliance_fixture("supermercados")
    pages = [PdfPage(**value["pages"][0])]
    assert parse_ueno_legal_tables(pages, value["source_url"], "Supermercados") == []


def test_changed_annex_wording_does_not_publish_a_generic_installment_category():
    value = alliance_fixture("cuotas-sin-intereses")
    page = PdfPage(**value["pages"][0])
    page.text = page.text.replace("Anexo I", "listado oficial de comercios adheridos")
    assert parse_ueno_legal_tables([page], value["source_url"], "Comercios Adheridos") == []


def test_actual_childrens_day_contract_has_continuous_application_and_personalized_balance():
    offers = parsed("pending-day-children")
    assert len(offers) == 60  # Twelve actual merchants, five legal levels each.
    assert all(o.publication == "confirmed" and o.schedule.kind == "all_days" for o in offers)
    assert all(o.valid_from == date(2026, 8, 14) and o.valid_until == date(2026, 8, 16) for o in offers)
    assert all(o.eligibility.personalization_required for o in offers)
    assert all(any("saldo promedio" in c.lower() for c in o.eligibility.conditions) for o in offers)
    assert all(o.benefits[0].percentage == 20 for o in offers)
    assert {o.evidence[1].page for o in offers} == {3}


def test_actual_sports_range_and_cap_period_do_not_prove_daily_application():
    offers = parsed("pending-sports")
    assert len(offers) == 80
    assert all(o.validity_state == "known" and o.schedule.state == "unknown" for o in offers)
    assert all(o.publication == "pending" for o in offers)


def test_actual_black_wellness_joins_card_matrix_to_merchants_and_optional_financing():
    offers = parsed("pending-black-wellness")
    assert len(offers) == 10
    assert len({o.merchant_name for o in offers}) == 5
    assert all(o.eligibility.cards == ["Mastercard Dúo Black", "Mastercard Dúo Ultra Black"] for o in offers)
    assert all(o.eligibility.card_types == ["credit"] and o.eligibility.levels == [] for o in offers)
    cashback = [o for o in offers if o.benefits[0].type == "cashback"]
    financing = [o for o in offers if o.benefits[0].type == "installments"]
    assert all([c.amount for c in o.caps] == [1500000, 375000] for o in cashback)
    assert all(o.benefits[0].percentage == 25 for o in cashback)
    assert len(financing) == 5 and all(o.benefits[0].installments == 12 and o.caps == [] for o in financing)
    assert all(o.publication == "confirmed" and len(o.benefits) == 1 for o in offers)
    assert all(o.evidence[0].page == 1 and o.evidence[1].page == 2 for o in offers)


def test_actual_black_shops_cda_conditions_keep_card_and_caps_in_their_own_rows():
    offers = parsed("pending-black-shops-cda")
    assert len(offers) == 156  # Thirty-nine merchants, three cashback rows and independent financing.
    assert len({o.key for o in offers}) == len(offers)
    merchant = [o for o in offers if o.merchant_name == "ALMACÉN 24/7" and o.benefits[0].type == "cashback"]
    assert len(merchant) == 3
    ultra = next(o for o in merchant if o.eligibility.cards == ["Mastercard Dúo Ultra Black"])
    black = [o for o in merchant if o.eligibility.cards == ["Mastercard Dúo Black"]]
    assert [c.amount for c in ultra.caps] == [4000000, 1600000]
    assert any(c.endswith("No Aplica") for c in ultra.eligibility.conditions if c.startswith("CDAs"))
    assert sorted([c.amount for c in o.caps] for o in black) == [[1000000, 400000], [4000000, 1600000]]
    assert all(any("450.000.000" in c and "75.000" in c for c in o.eligibility.conditions) for o in black)
    assert all(o.benefits[0].percentage == 40 for o in offers if o.benefits[0].type == "cashback")
    financing = [o for o in offers if o.benefits[0].type == "installments"]
    assert len(financing) == 39
    assert all(o.eligibility.cards == ["Mastercard Dúo Black", "Mastercard Dúo Ultra Black"] for o in financing)
    assert all(o.caps == [] and not any(c.startswith("CDAs vigentes") for c in o.eligibility.conditions) for o in financing)
    assert all(o.publication == "confirmed" for o in offers)


def test_actual_black_cuadrita_purchase_weekdays_override_continuous_benefit_template():
    offers = parsed("pending-black-cuadrita")
    assert len(offers) == 3
    assert all(o.schedule.kind == "weekly" and o.schedule.weekdays == [3, 4, 5, 6] for o in offers)
    assert all(o.publication == "confirmed" for o in offers)
    assert all(c.period == "month" for o in offers for c in o.caps)


def test_black_matrix_requires_its_annex_and_never_publishes_the_category():
    value = alliance_fixture("pending-black-wellness")
    pages = [PdfPage(**value["pages"][0])]
    assert parse_ueno_legal_tables(pages, value["source_url"], "BLACK BIENESTAR") == []


def test_black_matrix_unknown_card_or_conflicting_duplicate_rows_are_not_interpreted():
    value = alliance_fixture("pending-black-wellness")
    pages = [PdfPage(**p) for p in value["pages"]]
    pages[0].tables[0][1][0] = "Tarjeta por confirmar"
    assert parse_ueno_legal_tables(pages, value["source_url"], "BLACK BIENESTAR") == []
    pages = [PdfPage(**p) for p in value["pages"]]
    row = deepcopy(pages[0].tables[0][1])
    row[1] = "40%"
    pages[0].tables[0].append(row)
    assert parse_ueno_legal_tables(pages, value["source_url"], "BLACK BIENESTAR") == []


def test_black_matrix_replay_and_duplicate_annex_rows_preserve_offer_keys():
    value = alliance_fixture("pending-black-shops-cda")
    pages = [PdfPage(**p) for p in value["pages"]]
    before = parse_ueno_legal_tables(pages, value["source_url"], "BLACK TIENDAS")
    pages[2].tables[0].append(deepcopy(pages[2].tables[0][2]))
    after = parse_ueno_legal_tables(pages, value["source_url"], "BLACK TIENDAS")
    assert {o.key for o in after} == {o.key for o in before}
    assert len(after) == len(before)


def test_actual_black_della_no_purchase_cap_does_not_become_numeric_cap():
    offers = parsed("pending-black-della")
    assert len(offers) == 2
    assert all(o.caps == [] and o.publication == "confirmed" for o in offers)
    assert all(any("Sin tope" in c for c in o.eligibility.conditions) for o in offers)
    assert offers[1].benefits[0].installments == 15
    assert all(any("Rolex" in c and "sitio web" in c for c in o.eligibility.conditions) for o in offers)


def test_actual_free_spirit_uses_named_condition_and_infonet_without_upay_financing():
    offers = parsed("pending-black-free-spirit")
    assert len(offers) == 1
    assert offers[0].merchant_name == "Free Spirit"
    assert offers[0].eligibility.processors == ["Infonet"]
    assert offers[0].benefits[0].percentage == 40
    assert offers[0].publication == "confirmed"


def test_actual_kingo_exact_dates_include_both_shared_year_dates_and_balance_requirement():
    offers = parsed("pending-kingo")
    assert len(offers) == 5
    assert all(o.schedule.dates == [date(2026, 10, 3), date(2026, 10, 17)] for o in offers)
    assert all(o.valid_from == date(2026, 10, 3) and o.valid_until == date(2026, 10, 17) for o in offers)
    assert all(o.publication == "confirmed" and o.eligibility.personalization_required for o in offers)


def test_actual_cuadrita_annex_disagrees_with_contract_start_and_stays_pending():
    offers = parsed("pending-cuadrita-conflict")
    assert len(offers) == 10
    assert all(o.validity_state == "conflict" and o.publication == "pending" for o in offers)


def test_actual_power_impossible_september_31_is_not_silently_repaired():
    value = alliance_fixture("pending-power-invalid")
    pages = [PdfPage(**p) for p in value["pages"]]
    offers = parse_ueno_tables(pages, value["source_url"])
    assert len(offers) == 66
    assert all(o.publication == "pending" and o.validity_state != "known" for o in offers)
    assert all(o.valid_from is None and o.valid_until is None for o in offers)


def test_unrecognized_single_percentage_card_table_is_pending_even_with_explicit_calendar(monkeypatch):
    value = alliance_fixture("pending-black-cuadrita")
    pages = [PdfPage(**p) for p in value["pages"]]
    pages[0].tables[0][1][0] = "Tarjeta desconocida"
    monkeypatch.setattr("app.scraping.parsers.ueno_html_parser.read_pdf", lambda document: pages)
    source = ScrapedSource("html", value["detail_url"], text="<h1>Beneficio BLACK LA CUADRITA</h1>",
                           documents=[ScrapedSource("pdf", value["source_url"], text="fixture")])
    promotions = UenoHtmlParser().parse(source)
    assert all(o.publication == "pending" for p in promotions for o in p.offers)
    assert all(p.metadata["offers_complete"] == "false" for p in promotions)


@pytest.mark.parametrize("annex_interval,expected_state", [
    ("Desde el 30 de septiembre del 2026 hasta el 31 de octubre del 2026", "conflict"),
    ("Desde el 01 de octubre del 2026 hasta el 01 de noviembre del 2026", "conflict"),
    ("Desde el 02 de octubre del 2026 hasta el 30 de octubre del 2026", "known"),
])
def test_fully_specified_annex_range_must_fit_its_parent_contract(annex_interval, expected_state):
    value = alliance_fixture("pending-black-wellness")
    pages = [PdfPage(**p) for p in value["pages"]]
    # Explicitly mutate one actual annex row; the source contract says Oct 1–31.
    row = pages[1].tables[0][2]
    merchant = row[1]
    row[4] = annex_interval
    offers = parse_ueno_legal_tables(pages, value["source_url"], "BLACK BIENESTAR")
    affected = [o for o in offers if o.merchant_name == merchant]
    assert len(affected) == 2  # Cashback and independently offered installments.
    assert all(o.validity_state == expected_state for o in affected)
    assert all(o.publication == ("confirmed" if expected_state == "known" else "pending") for o in affected)
    assert all(annex_interval in o.evidence[1].text for o in affected)
    assert all(o.publication == "confirmed" for o in offers if o.merchant_name != merchant)


@pytest.mark.parametrize("parent_interval", [
    "Desde el 01 de octubre hasta el 31 de octubre.",
    "Desde el 01 de septiembre hasta el 31 de septiembre del 2026.",
])
def test_explicit_annex_dates_cannot_repair_missing_or_invalid_parent_validity(parent_interval):
    value = alliance_fixture("pending-black-wellness")
    pages = [PdfPage(**p) for p in value["pages"]]
    pages[0].text = pages[0].text.replace("Desde el 01 de octubre hasta el 31 de octubre del 2026.", parent_interval)
    row = pages[1].tables[0][2]
    merchant = row[1]
    row[4] = "Desde el 01 de octubre del 2026 hasta el 31 de octubre del 2026"
    offers = parse_ueno_legal_tables(pages, value["source_url"], "BLACK BIENESTAR")
    affected = [o for o in offers if o.merchant_name == merchant]
    assert len(affected) == 2
    assert all(o.validity_state == "unknown" and o.publication == "pending" for o in affected)
    # The fully specified annex dates are retained for review, never published.
    assert all(o.valid_from == date(2026, 10, 1) and o.valid_until == date(2026, 10, 31) for o in affected)
