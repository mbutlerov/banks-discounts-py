"""Itau source, parser, document and catalogue regressions."""
from __future__ import annotations

import unittest

import pytest
from bs4 import BeautifulSoup

from app.scraping.clients.http_client import HttpClientError
from app.scraping.parsers.itau_html_parser import ItauHtmlParser, _purchase_days_text
from app.scraping.schemas import ScrapedSource
from app.scraping.sources.helpers import SourceDiscoveryError
from app.scraping.sources.itau_html_source import (
    BASE_URL, DETAIL_URL, LEGACY_URL, MAIN_URL, ItauHtmlSource, extract_detail_calls,
)
from app.scraping.utils.schedule import extract_schedule
from tests.helpers.sources import FakeHttp, catalog_fixture, parser_fixture


class SourceContractTests(unittest.TestCase):
    def test_itau_hidden_groups_and_multi_category_deduplicate_b_c(self):
        base = "https://www.itau.com.py"
        http = FakeHttp({base + "/beneficios": '<a href="/beneficios2/categoria/7">Gastronomía</a><a href="/beneficios2/categoria/22">Débito</a>',
                         base + "/beneficios2/categoria/7": '''<div data-page="2" style="display:none"><a onclick="buscar('1','2','A')">A</a></div>''',
                         base + "/beneficios2/categoria/22": '''<a onclick="buscar('1','2','A')">A</a>''',
                         base + "/beneficios2/Detalle?b=1&c=2": "<h2>A</h2>"})
        sources = ItauHtmlSource(http).fetch()
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].metadata["source_categories"], "Gastronomía | Débito")


class OfficialParserTests(unittest.TestCase):
    def test_itau_keeps_source_ids_card_variants_and_conflicting_dates(self):
        source = ScrapedSource("html", "https://www.itau.com.py/beneficios2/Detalle?b=17050&c=30789",
                               text=parser_fixture("itau_tatakua.html"),
                               metadata={"b": "17050", "c": "30789", "partner_name": "Tatakua", "category_name": "Gastronomía"})
        promo = ItauHtmlParser().parse(source)[0]
        self.assertEqual(promo.source_key, "itau:17050:30789")
        self.assertEqual(promo.category_name, "Gastronomía")
        self.assertEqual([o.benefits[0].percentage for o in promo.offers], [25, 30])
        self.assertEqual(promo.offers[0].eligibility.cards, [])
        self.assertIn("Visa Infinite", promo.offers[1].eligibility.cards)
        self.assertTrue(all(o.schedule.weekdays == [4] for o in promo.offers))
        self.assertTrue(all(o.validity_state == "conflict" and o.publication == "pending" for o in promo.offers))
        self.assertIsNone(promo.discount_percentage)
        self.assertTrue(any("2026-09-17" in e.text for e in promo.offers[0].evidence))
        self.assertTrue(any("1 de octubre" in e.text for e in promo.offers[0].evidence))

    def test_itau_unseparated_tiers_are_pending(self):
        html = '<h2>Comercio</h2><p>Todos los jueves.</p><li><strong>beneficio:</strong>20% de ahorro con crédito y 30% de ahorro con VISA INFINITE</li><li><strong>vigencia:</strong>2026-10-01 hasta 2026-10-31</li><li><strong>medios de pago:</strong>tarjeta de crédito</li>'
        source = ScrapedSource("html", "https://www.itau.com.py/beneficios2/Detalle?b=1&c=2", text=html)
        offer = ItauHtmlParser().parse(source)[0].offers[0]
        self.assertEqual(offer.publication, "pending")
        self.assertEqual(source.metadata["parse_warning"], "itau_unresolved_card_variants")


def test_itau_official_landing_resolves_actual_category_cards():
    landing = catalog_fixture("itau_landing.html")
    category_urls = {BASE_URL + str(a["href"]) for a in BeautifulSoup(landing, "html.parser").find_all("a", href=True)}
    responses = {url: "<h2>Sin promociones</h2>" for url in category_urls}
    responses.update({
        MAIN_URL: landing,
        BASE_URL + "/beneficios2/categoria/7": catalog_fixture("itau_category_7.html"),
        DETAIL_URL.format(b="17050", c="30789"): "<h2>Tatakua</h2>",
        DETAIL_URL.format(b="17051", c="32058"): catalog_fixture("itau_queseria.html"),
    })
    http = FakeHttp(responses)
    sources = ItauHtmlSource(http).fetch()
    assert http.urls[0] == MAIN_URL
    assert len(category_urls) == 34
    assert {(s.metadata["b"], s.metadata["c"]) for s in sources} == {("17050", "30789"), ("17051", "32058")}
    assert all(s.metadata["category_name"] == "Gastronomía" for s in sources)
    assert all("discovery_warning" not in s.metadata for s in sources)


def test_itau_hidden_cards_quote_names_and_direct_links_preserve_ids():
    soup = BeautifulSoup('''<div style="display:none" data-page="2">
        <a onclick="buscar('1','2','O\\'Hara &amp; Café')">A</a>
        <a href="/beneficios2/Detalle?c=4&amp;b=3">Comercio B</a>
        <a href="https://example.com/beneficios2/Detalle?b=8&amp;c=9">Fuera del banco</a>
        <script>buscar('5','6','Plantilla ajena a las tarjetas')</script>
        </div>''', "html.parser")
    assert extract_detail_calls(soup) == [("1", "2", "O'Hara & Café"), ("3", "4", "Comercio B")]


def test_itau_legacy_fallback_is_explicit_and_preserves_actual_source():
    detail = DETAIL_URL.format(b="1", c="2")
    http = FakeHttp({MAIN_URL: HttpClientError("HTTP 503"), LEGACY_URL: '<a onclick="buscar(\'1\',\'2\',\'A\')">A</a>', detail: "<h2>A</h2>"})
    source = ItauHtmlSource(http).fetch()[0]
    assert source.metadata["catalogue_url"] == LEGACY_URL
    assert "entrada /beneficios" in source.metadata["discovery_warning"]


def test_itau_large_catalogue_is_not_truncated_at_120():
    cards = "".join(f'<a onclick="buscar(\'{i}\',\'2\',\'A{i}\')">A{i}</a>' for i in range(1, 122))
    responses = {MAIN_URL: cards, **{DETAIL_URL.format(b=str(i), c="2"): f"<h2>A{i}</h2>" for i in range(1, 122)}}
    sources = ItauHtmlSource(FakeHttp(responses)).fetch()
    assert len(sources) == 121
    assert "discovery_warning" not in sources[0].metadata


def test_itau_truncated_catalogue_and_failed_categories_are_partial():
    category = BASE_URL + "/beneficios2/categoria/7"
    http = FakeHttp({
        MAIN_URL: f'<a href="{category}">Gastronomía</a><a onclick="buscar(\'1\',\'2\',\'A\')">A</a><a onclick="buscar(\'3\',\'4\',\'B\')">B</a>',
        category: HttpClientError("HTTP 500"),
        DETAIL_URL.format(b="1", c="2"): "<h2>A</h2>",
    })
    source = ItauHtmlSource(http, max_details=1).fetch()[0]
    assert "categoría" in source.metadata["discovery_warning"]
    assert "1 de 2" in source.metadata["discovery_warning"]


def test_itau_all_details_failed_is_an_explicit_acquisition_error():
    http = FakeHttp({MAIN_URL: '<a onclick="buscar(\'1\',\'2\',\'A\')">A</a>', DETAIL_URL.format(b="1", c="2"): HttpClientError("HTTP 502")})
    with pytest.raises(SourceDiscoveryError, match="ningún detalle"):
        ItauHtmlSource(http).fetch()


def test_itau_changed_card_route_is_partial_instead_of_disappearing():
    category = BASE_URL + "/beneficios2/categoria/7"
    http = FakeHttp({
        MAIN_URL: f'<a href="{category}">Gastronomía</a>',
        category: '<a class="item-oferta" onclick="buscar(\'1\',\'2\',\'A\')">A</a><a class="item-oferta" onclick="abrirBeneficio(3)">Nueva plantilla</a>',
        DETAIL_URL.format(b="1", c="2"): "<h2>A</h2>",
    })
    source = ItauHtmlSource(http).fetch()[0]
    assert "1 tarjetas con rutas no reconocidas" in source.metadata["discovery_warning"]


def test_actual_queseria_keeps_cap_branch_evidence_and_validity_conflict():
    source = ScrapedSource("html", DETAIL_URL.format(b="17051", c="32058"),
                           text=catalog_fixture("itau_queseria.html"),
                           metadata={"b": "17051", "c": "32058", "partner_name": "LA QUESERIA", "category_name": "Gastronomía"})
    promotion = ItauHtmlParser().parse(source)[0]
    assert promotion.source_key == "itau:17051:32058"
    assert [o.benefits[0].percentage for o in promotion.offers] == [15, 20]
    assert all(o.schedule.weekdays == [1, 2, 3] for o in promotion.offers)
    assert all(o.publication == "pending" and o.validity_state == "conflict" for o in promotion.offers)
    base, shopping = promotion.offers
    assert base.caps[0].amount == 700000
    assert base.caps[0].type == "purchase"
    assert base.caps[0].period == "unknown"
    assert shopping.caps == []
    assert shopping.eligibility.locations == ["Shopping Mariscal"]
    assert all(any(e.field == "benefit" for e in o.evidence) for o in promotion.offers)
    assert any(e.field == "cap" and "700.000" in e.text for e in base.evidence)


@pytest.mark.parametrize("text,kind,weekdays,month_days", [
    ("Todos los días del 1 de octubre al 31 de octubre 2026.", "all_days", [], []),
    ("Del 1 al 31 de octubre de 2026, todos los días.", "all_days", [], []),
    ("Del 1 de octubre al 31 de octubre 2026 los fines de semana.", "weekly", [6, 7], []),
    ("Del 1 de octubre al 31 de octubre 2026. De lunes a miércoles.", "weekly", [1, 2, 3], []),
    ("Desde el 1 de octubre de 2026 hasta el 31 de octubre de 2026 el 01 de cada mes.", "monthly_day", [], [1]),
    ("2026-10-01 hasta 2026-10-31 todos los días.", "all_days", [], []),
])
def test_itau_validity_bounds_do_not_remove_explicit_purchase_rules(text, kind, weekdays, month_days):
    schedule = extract_schedule(_purchase_days_text(text), dedicated=True)
    assert (schedule.state, schedule.kind, schedule.weekdays, schedule.month_days) == ("known", kind, weekdays, month_days)


def test_itau_validity_alone_never_becomes_a_purchase_date():
    schedule = extract_schedule(_purchase_days_text("Del 1 de octubre al 31 de octubre 2026."), dedicated=True)
    assert schedule.state == "unknown"


def test_itau_purchase_date_after_validity_range_survives():
    schedule = extract_schedule(_purchase_days_text("Del 1 al 31 de octubre 2026, aplica únicamente el sábado 3 de octubre de 2026."), dedicated=True)
    assert schedule.state == "known"
    assert schedule.kind == "specific_dates"
    assert [d.isoformat() for d in schedule.dates] == ["2026-10-03"]


def _pending_fixture(name, *, b, c, merchant):
    source = ScrapedSource("html", DETAIL_URL.format(b=b, c=c), text=parser_fixture(name),
                           metadata={"b": b, "c": c, "partner_name": merchant})
    return source, ItauHtmlParser().parse(source)[0]


def test_actual_hering_wallet_bonus_is_separate_from_base_purchase_channel():
    source, promotion = _pending_fixture("itau_pending_wallet_hering.html", b="16965", c="1600", merchant="Hering")
    base, wallet = promotion.offers
    assert promotion.source_key == "itau:16965:1600"
    assert base.key == "831940f948a3ecd19f5e297d"  # Existing rule retains its identity.
    assert [o.benefits[0].percentage for o in promotion.offers] == [15, 20]
    assert all(o.benefits[1].installments == 6 for o in promotion.offers)
    assert base.eligibility.channels == []
    assert wallet.eligibility.channels == ["Apple Pay", "Google Pay"]
    assert all(o.eligibility.locations == ["Shopping Mariano"] for o in promotion.offers)
    assert all(o.schedule.kind == "monthly_nth_weekday" and o.schedule.ordinal == 1
               and o.schedule.weekdays == [3] for o in promotion.offers)
    assert all(o.publication == "confirmed" for o in promotion.offers)
    assert promotion.discount_percentage is None
    assert promotion.metadata["offers_complete"] == "true"
    assert "parse_warning" not in source.metadata
    assert any("5%" in condition for condition in wallet.benefits[0].conditions)


def test_actual_billetera_expansion_uses_only_corroborated_named_wallets():
    _, promotion = _pending_fixture("itau_pending_wallet_indio.html", b="16312", c="31092", merchant="Indio Termos")
    assert [o.benefits[0].percentage for o in promotion.offers] == [20, 25]
    assert promotion.offers[0].eligibility.channels == []
    assert promotion.offers[1].eligibility.channels == ["Apple Pay", "Google Pay"]
    assert all("QR" not in o.eligibility.channels for o in promotion.offers)


def test_actual_full_wallet_detail_snapshots_survive_source_fetch_and_parser_pipeline():
    category = BASE_URL + "/beneficios2/categoria/3"
    http = FakeHttp({
        MAIN_URL: f'<a href="{category}">Varios</a>',
        category: '<a onclick="buscar(\'16965\',\'1600\',\'Hering\')">Hering</a><a onclick="buscar(\'16312\',\'31092\',\'Indio Termos\')">Indio Termos</a>',
        DETAIL_URL.format(b="16965", c="1600"): parser_fixture("itau_pending_wallet_hering.html"),
        DETAIL_URL.format(b="16312", c="31092"): parser_fixture("itau_pending_wallet_indio.html"),
    })
    sources = ItauHtmlSource(http).fetch()
    promotions = [ItauHtmlParser().parse(source)[0] for source in sources]
    assert len(promotions) == 2
    assert [p.source_key for p in promotions] == ["itau:16965:1600", "itau:16312:31092"]
    assert [[o.benefits[0].percentage for o in p.offers] for p in promotions] == [[15, 20], [20, 25]]
    assert all(o.publication == "confirmed" for p in promotions for o in p.offers)
    assert all(o.eligibility.locations == ["Shopping Mariano"] for p in promotions for o in p.offers)


def test_wallet_rule_keys_are_repeatable_and_do_not_depend_on_percentage():
    source, initial = _pending_fixture("itau_pending_wallet_hering.html", b="16965", c="1600", merchant="Hering")
    assert ItauHtmlParser().parse(source)[0] == initial
    source.text = source.text.replace("15%", "17%")
    changed = ItauHtmlParser().parse(source)[0]
    assert [o.key for o in changed.offers] == [o.key for o in initial.offers]
    assert [o.benefits[0].percentage for o in changed.offers] == [17, 22]


def test_actual_differing_intro_and_labelled_percentages_remain_pending():
    source, promotion = _pending_fixture("itau_pending_benefit_conflict_bertoni.html", b="16903", c="30201", merchant="Bertoni")
    assert len(promotion.offers) == 1
    assert promotion.offers[0].publication == "pending"
    assert source.metadata["parse_warning"] == "itau_conflicting_benefit_fields"
    assert promotion.metadata["offers_complete"] == "false"
    assert any("10%" in e.text for e in promotion.offers[0].evidence)
    assert any("15%" in e.text for e in promotion.offers[0].evidence)


def test_actual_monthly_campaign_without_purchase_days_stays_pending():
    _, promotion = _pending_fixture("itau_pending_missing_schedule_syrocco.html", b="16108", c="1477", merchant="Syrocco")
    offer = promotion.offers[0]
    assert offer.validity_state == "known"
    assert offer.schedule.state == "unknown"
    assert offer.publication == "pending"
    # Other merchants' percentages in shared boilerplate do not alter Syrocco.
    assert offer.benefits[0].percentage == 20
    assert "parse_warning" not in promotion.metadata


def test_actual_intro_card_context_does_not_assign_premium_installments_to_all_cards():
    _, promotion = _pending_fixture("itau_pending_card_context_origen.html", b="16056", c="1294", merchant="Origen")
    offer = promotion.offers[0]
    assert offer.benefits[0].installments == 12
    assert set(offer.eligibility.cards) == {"Visa Infinite", "American Express Platinum"}
    assert "Mastercard Black" not in offer.eligibility.cards  # Points-earning boilerplate.
    assert offer.publication == "pending"  # No explicit purchase calendar.
    assert any(e.field == "cards" and "The Platinum Card" in e.text for e in offer.evidence)


@pytest.mark.parametrize("condition", ["Google Pay solo con Visa Infinite", "Visa", "Google Pay en clientes seleccionados"])
def test_wallet_bonus_with_unhandled_additional_eligibility_stays_pending(condition):
    clause = f"20% de ahorro mas 5% pagando con {condition} y hasta 6 cuotas sin intereses"
    html = f"<h2>Comercio</h2><p>Todos los jueves. {clause}.</p><li><strong>beneficio:</strong>{clause}</li><li><strong>vigencia:</strong>2026-10-01 hasta 2026-10-31</li><li><strong>medios de pago:</strong>tarjeta de crédito</li>"
    source = ScrapedSource("html", DETAIL_URL.format(b="1", c="2"), text=html)
    promotion = ItauHtmlParser().parse(source)[0]
    assert len(promotion.offers) == 1
    assert promotion.offers[0].publication == "pending"
    assert source.metadata["parse_warning"] == "itau_unresolved_card_variants"


def test_split_wallet_bonus_preserves_real_validity_conflicts():
    source, _ = _pending_fixture("itau_pending_wallet_hering.html", b="16965", c="1600", merchant="Hering")
    source.text = source.text.replace("Hasta el 2 de diciembre de 2026", "Hasta el 3 de diciembre de 2026")
    promotion = ItauHtmlParser().parse(source)[0]
    assert len(promotion.offers) == 2
    assert all(o.validity_state == "conflict" and o.publication == "pending" for o in promotion.offers)


def test_split_wallet_bonus_does_not_create_missing_purchase_days():
    source, _ = _pending_fixture("itau_pending_wallet_hering.html", b="16965", c="1600", merchant="Hering")
    source.text = source.text.replace("Aplica el primer mi&#233;rcoles del mes.", "")
    promotion = ItauHtmlParser().parse(source)[0]
    assert len(promotion.offers) == 2
    assert all(o.schedule.state == "unknown" and o.publication == "pending" for o in promotion.offers)


@pytest.mark.parametrize("intro_clause", [
    "5% de ahorro mas 20% pagando con Google Pay",
    "20% de ahorro mas 5% pagando con Apple Pay",
])
def test_wallet_bonus_roles_and_channels_must_agree_between_source_fields(intro_clause):
    html = f"<h2>Comercio</h2><p>Todos los jueves. {intro_clause}.</p><li><strong>beneficio:</strong>20% de ahorro mas 5% pagando con Google Pay</li><li><strong>vigencia:</strong>2026-10-01 hasta 2026-10-31</li><li><strong>medios de pago:</strong>tarjeta de crédito</li>"
    source = ScrapedSource("html", DETAIL_URL.format(b="1", c="2"), text=html)
    promotion = ItauHtmlParser().parse(source)[0]
    assert len(promotion.offers) == 1
    assert promotion.offers[0].publication == "pending"
    assert source.metadata["parse_warning"] == "itau_conflicting_benefit_fields"


@pytest.mark.parametrize("text", [
    "Del 5 al 18 octubre 2026.",
    "Del martes 6 de enero al 1 de diciembre de 2026.",
])
def test_itau_compact_or_weekday_prefixed_validity_never_becomes_purchase_days(text):
    assert extract_schedule(_purchase_days_text(text), dedicated=True).state == "unknown"
