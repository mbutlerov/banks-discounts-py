"""Gnb source, parser, document and catalogue regressions."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import unittest
import json

import pytest

from app.scraping.clients.http_client import HttpClientError
from app.scraping.parsers.gnb_html_parser import parse_gnb_pdf
from app.scraping.parsers.pdf_offers import PdfPage
from app.scraping.sources.gnb_html_source import GnbHtmlSource, OFFICIAL_CARDS_URL, PORTAL_URL, PUBLIC_API_URL, API_PAGE_SIZE
from app.scraping.sources.helpers import SourceDiscoveryError
from tests.helpers.sources import FakeHttp, parser_fixture


class SourceContractTests(unittest.TestCase):
    def test_blocked_gnb_is_an_error_and_never_an_empty_success(self):
        http = FakeHttp({OFFICIAL_CARDS_URL: f'<a href="{PORTAL_URL}">Beneficios tarjeta de crédito</a>',
                         PORTAL_URL: HttpClientError("HTTP 403")})
        with self.assertRaises(HttpClientError):
            GnbHtmlSource(http).fetch()
        self.assertEqual(http.urls, [OFFICIAL_CARDS_URL, PORTAL_URL])


class OfficialParserTests(unittest.TestCase):
    def test_gnb_expired_fixture_preserves_prepaid_and_credit_tiers(self):
        offers = parse_gnb_pdf([PdfPage(1, parser_fixture("gnb_popeyes.txt"))],
                              "https://www.beneficiosbancognb.com.py/imagenes/5009_bases-y-condiciones-popeyes.pdf", "Popeyes")
        self.assertEqual([o.benefits[0].percentage for o in offers], [25, 20, 10])
        self.assertEqual(offers[2].eligibility.card_types, ["prepaid"])
        self.assertEqual(offers[2].caps, [])
        self.assertEqual(offers[0].caps[0].amount, Decimal(600000))
        self.assertEqual(offers[0].valid_until, date(2026, 6, 30))
        self.assertTrue(all(o.schedule.weekdays == [1, 2] for o in offers))


def test_gnb_uses_current_production_route_when_bank_links_legacy_root():
    http = FakeHttp({OFFICIAL_CARDS_URL: '<a href="https://www.beneficiosbancognb.com.py/">Beneficios</a>', PORTAL_URL: HttpClientError("HTTP 403")})
    with pytest.raises(HttpClientError, match="403"):
        GnbHtmlSource(http).fetch()
    assert http.urls == [OFFICIAL_CARDS_URL, PORTAL_URL]


def test_gnb_auxiliary_page_failure_does_not_hide_portal_status():
    http = FakeHttp({OFFICIAL_CARDS_URL: HttpClientError("HTTP 503"), PORTAL_URL: HttpClientError("HTTP 403")})
    with pytest.raises(HttpClientError, match="403"):
        GnbHtmlSource(http).fetch()
    assert http.urls == [OFFICIAL_CARDS_URL, PORTAL_URL]


class GnbApiParserTests(unittest.TestCase):
    """Verified detail-JSON fixtures stay independent of network acquisition."""

    def record(self, promo_id=639):
        import json
        value = json.loads(parser_fixture("gnb_api_details.json"))
        return next(record for record in value["data"] if record["id"] == promo_id)

    def source(self, record=None):
        import json
        from app.scraping.schemas import ScrapedSource
        from app.scraping.sources.gnb_html_source import PUBLIC_API_URL
        record = self.record() if record is None else record
        return ScrapedSource(source_type="json", source_url=f"https://www.beneficiosbancognb.com.py/v2/beneficios/categorias/{record['id']}",
                             text=json.dumps({"data": record}), metadata={"source_type": "gnb_api", "promo_id": str(record["id"]),
                             "api_url": f"{PUBLIC_API_URL}/benefits?pageSize=999&paginationKey=0"})

    def parse(self, source):
        from app.scraping.parsers.gnb_html_parser import GnbHtmlParser
        return GnbHtmlParser().parse(source)[0]

    def test_api_json_separates_base_cashback_and_explicit_qr_increment(self):
        source = self.source()
        promotion = self.parse(source)
        base, qr = promotion.offers
        self.assertEqual([offer.benefits[0].percentage for offer in promotion.offers], [20, 25])
        self.assertTrue(all(offer.publication == "confirmed" for offer in promotion.offers))
        self.assertTrue(all(offer.schedule.weekdays == [3, 4, 5, 6, 7] for offer in promotion.offers))
        self.assertEqual(base.eligibility.channels, [])
        self.assertEqual(qr.eligibility.channels, ["QR", "app"])
        self.assertEqual(base.caps[0].amount, Decimal(1000000))
        self.assertEqual(qr.caps, base.caps)
        self.assertEqual(base.valid_from, date(2026, 7, 1))
        self.assertEqual(promotion.category_name, "gastronomy")
        self.assertEqual(promotion.source_key, "gnb:639")
        self.assertEqual(source.metadata["offers_complete"], "true")
        self.assertTrue(any(e.field == "benefit" and "adicional" in e.text for e in qr.evidence))
        self.assertTrue(any("No aplica" in condition for condition in qr.eligibility.conditions))

    def test_api_date_contradiction_is_pending_despite_active_status(self):
        source = self.source(self.record(175))
        offer = self.parse(source).offers[0]
        self.assertEqual(offer.valid_from, date(2026, 4, 2))
        self.assertEqual(offer.valid_until, date(2027, 2, 28))
        self.assertEqual(offer.validity_state, "conflict")
        self.assertEqual(offer.publication, "pending")
        self.assertEqual(offer.schedule.weekdays, [4, 5, 6, 7])
        self.assertEqual(offer.eligibility.card_types, ["credit", "prepaid"])
        self.assertIn("gnb_api_validity_conflict", source.metadata["parse_warning"])
        self.assertTrue(any("2025-01-03" in e.text for e in offer.evidence))

    def test_card_tiers_do_not_inherit_promotional_maximum(self):
        source = self.source(self.record(260))
        premium, classic = self.parse(source).offers
        self.assertEqual(premium.benefits[0].percentage, 25)
        self.assertEqual(classic.benefits[0].percentage, 20)
        self.assertIn("Mastercard Metalcard Premier", premium.eligibility.cards)
        self.assertNotIn("Mastercard Metalcard Premier", classic.eligibility.cards)
        self.assertEqual(classic.schedule.weekdays, [6, 7])
        self.assertIn("delivery propio", " ".join(classic.eligibility.conditions))

    def test_discount_weekdays_and_financing_all_days_are_separate(self):
        source = self.source(self.record(620))
        premium, classic, financing = self.parse(source).offers
        self.assertEqual([premium.benefits[0].percentage, classic.benefits[0].percentage], [25, 20])
        self.assertEqual(premium.schedule.weekdays, [4, 5])
        self.assertEqual(classic.schedule.weekdays, [4, 5])
        self.assertEqual(financing.benefits[0].type, "installments")
        self.assertEqual(financing.benefits[0].installments, 6)
        self.assertEqual(financing.schedule.kind, "all_days")
        self.assertEqual(financing.caps, [])
        self.assertTrue(all(o.publication == "confirmed" for o in (premium, classic, financing)))

    def test_purchase_locations_have_their_own_discount_variants(self):
        source = self.source(self.record(534))
        asuncion, cde, financing = self.parse(source).offers
        self.assertEqual([asuncion.benefits[0].percentage, cde.benefits[0].percentage], [45, 20])
        self.assertEqual(asuncion.eligibility.cities, ["Asunción"])
        self.assertEqual(cde.eligibility.cities, ["Ciudad del Este"])
        self.assertEqual(asuncion.location_scope, "specified")
        self.assertNotEqual(asuncion.locations[0].key, cde.locations[0].key)
        self.assertEqual(financing.locations, [])
        self.assertTrue(all(o.schedule.weekdays == [5] for o in (asuncion, cde, financing)))

    def test_shared_month_in_explicit_purchase_dates_keeps_both_dates(self):
        source = self.source(self.record(232))
        offer = self.parse(source).offers[0]
        self.assertEqual(offer.schedule.kind, "specific_dates")
        self.assertEqual(offer.schedule.dates, [date(2026, 10, 7), date(2026, 10, 14)])
        self.assertEqual(offer.eligibility.channels, ["web", "app"])
        self.assertNotIn("debit", offer.eligibility.card_types)
        self.assertNotIn("prepaid", offer.eligibility.card_types)

    def test_explicit_list_with_wrong_weekday_stays_pending(self):
        record = self.record(232)
        record["description"] = record["description"].replace("7 y 14", "8 y 14")
        source = self.source(record)
        offer = self.parse(source).offers[0]
        self.assertEqual(offer.schedule.state, "conflict")
        self.assertEqual(offer.publication, "pending")

    def test_simple_installments_only_record_does_not_invent_discount(self):
        source = self.source(self.record(634))
        offer = self.parse(source).offers[0]
        self.assertEqual(offer.publication, "confirmed")
        self.assertEqual(offer.benefits[0].type, "installments")
        self.assertEqual(offer.benefits[0].installments, 12)
        self.assertEqual(offer.schedule.kind, "all_days")
        self.assertEqual(offer.eligibility.card_types, ["credit"])
        self.assertEqual(offer.caps, [])

    def test_unrelated_benefit_type_cannot_be_summed_into_base(self):
        record = self.record()
        record["description"] = record["description"].replace("+5% de reintegro adicional", "+5% de descuento adicional")
        source = self.source(record)
        base, extra = self.parse(source).offers
        self.assertEqual(base.benefits[0].percentage, 20)
        self.assertEqual(extra.benefits[0].percentage, 5)
        self.assertEqual(extra.publication, "pending")
        self.assertIn("gnb_api_additional_benefit_unresolved", source.metadata["parse_warning"])

    def test_extra_qr_for_other_card_cannot_inherit_base(self):
        record = self.record()
        record["description"] = record["description"].replace("con tarjetas de crédito Mastercard a través", "con tarjetas de crédito Visa a través")
        source = self.source(record)
        base, extra = self.parse(source).offers
        self.assertEqual(base.benefits[0].percentage, 20)
        self.assertEqual(extra.benefits[0].percentage, 5)
        self.assertEqual(extra.publication, "pending")

    def test_missing_purchase_calendar_stays_pending(self):
        record = self.record()
        record["description"] = record["description"].replace("de miércoles a domingos", "en locales adheridos")
        source = self.source(record)
        self.assertTrue(all(offer.publication == "pending" for offer in self.parse(source).offers))
        self.assertEqual(source.metadata["offers_complete"], "true")

    def test_repeated_success_refreshes_old_gnb_warning(self):
        source = self.source()
        source.metadata["parse_warning"] = "gnb_api_validity_conflict"
        self.parse(source)
        self.assertNotIn("parse_warning", source.metadata)

    def test_variant_identity_survives_a_percentage_change(self):
        first = self.parse(self.source())
        record = self.record()
        record["description"] = record["description"].replace("20% de reintegro", "18% de reintegro")
        second = self.parse(self.source(record))
        self.assertEqual([o.key for o in first.offers], [o.key for o in second.offers])
        self.assertEqual([o.benefits[0].percentage for o in second.offers], [18, 23])

    def test_document_download_error_prevents_confirmation(self):
        source = self.source()
        source.metadata["document_error"] = "HTTP 403 legal PDF"
        self.assertTrue(all(o.publication == "pending" for o in self.parse(source).offers))
        self.assertEqual(source.metadata["offers_complete"], "false")

    def test_readable_unrecognized_pdf_retains_verified_json_with_partial_source(self):
        from app.scraping.schemas import ScrapedSource
        source = self.source()
        source.documents = [ScrapedSource(source_type="pdf", source_url="https://www.beneficiosbancognb.com.py/imagenes/terms.pdf",
                                          text="Anexo de establecimientos sin plantilla de beneficios reconocible")]
        self.assertTrue(all(o.publication == "confirmed" for o in self.parse(source).offers))
        self.assertEqual(source.metadata["offers_complete"], "false")
        self.assertIn("gnb_pdf_template_unrecognized", source.metadata["parse_warning"])

    def test_legal_pdf_validity_contradiction_overrides_json_confirmation(self):
        from app.scraping.schemas import ScrapedSource
        source = self.source()
        source.documents = [ScrapedSource(source_type="pdf", source_url="https://www.beneficiosbancognb.com.py/imagenes/terms.pdf",
                                          text="Vigencia: Del 01 de julio al 30 de noviembre del 2026.")]
        offers = self.parse(source).offers
        self.assertTrue(all(o.publication == "pending" and o.validity_state == "conflict" for o in offers))
        self.assertIn("gnb_pdf_validity_conflict", source.metadata["parse_warning"])

    def test_inactive_api_record_stays_retired_even_with_document_error(self):
        record = self.record()
        record["status"] = False
        source = self.source(record)
        source.metadata["document_error"] = "HTTP 403"
        self.assertTrue(all(o.publication == "retired" for o in self.parse(source).offers))

    def test_listing_and_malformed_json_are_never_parsed_as_single_merchant(self):
        from app.scraping.parsers.gnb_html_parser import GnbHtmlParser
        source = self.source()
        source.text = parser_fixture("gnb_api_details.json")
        self.assertEqual(GnbHtmlParser().parse(source), [])
        self.assertEqual(source.metadata["parse_warning"], "gnb_api_requires_single_detail")
        source.text = "{invalid"
        self.assertEqual(GnbHtmlParser().parse(source), [])
        self.assertEqual(source.metadata["parse_warning"], "gnb_api_invalid_json")

    def test_qualified_caps_are_not_copied_to_other_card_tiers(self):
        record = self.record(620)
        record["description"] += "<li>Tope de compra mensual de Gs. 1.000.000 para tarjetas Mastercard Black; Gs. 500.000 para tarjetas Mastercard Clásica y Oro.</li>"
        source = self.source(record)
        premium, classic, financing = self.parse(source).offers
        self.assertEqual(premium.publication, "pending")
        self.assertEqual(classic.publication, "pending")
        self.assertEqual(financing.publication, "confirmed")
        self.assertIn("caps", premium.eligibility.unknown_fields)
        self.assertIn("gnb_api_cap_scope_unresolved", source.metadata["parse_warning"])
        self.assertTrue(all(cap.amount != Decimal(500000) for cap in premium.caps))

    def test_credit_account_cap_is_not_applied_to_prepaid_cards(self):
        record = self.record()
        record["description"] = record["description"].replace("con tarjetas de crédito Mastercard", "con tarjetas de crédito y prepagas Mastercard")
        source = self.source(record)
        self.assertTrue(all(o.publication == "pending" for o in self.parse(source).offers))
        self.assertIn("gnb_api_cap_scope_unresolved", source.metadata["parse_warning"])

    def test_purchase_and_equivalent_cashback_ceilings_keep_their_types(self):
        record = self.record()
        record["description"] += "<li>Tope de compra mensual por cuenta de tarjeta de crédito Gs. 2.000.000 equivalente a un reintegro máximo mensual de Gs. 400.000.</li>"
        offers = self.parse(self.source(record)).offers
        self.assertTrue(any(cap.type == "purchase" and cap.amount == Decimal(2000000) for cap in offers[0].caps))
        self.assertTrue(any(cap.type == "cashback" and cap.amount == Decimal(400000) for cap in offers[0].caps))

    def test_legal_tier_matching_requires_same_card_product(self):
        from unittest.mock import patch
        from app.scraping.schemas import ScrapedSource
        from app.scraping.utils.offers import make_offer
        source = self.source(self.record(620))
        source.documents = [ScrapedSource(source_type="pdf", source_url="https://www.beneficiosbancognb.com.py/imagenes/terms.pdf", text="Condiciones")]
        wrong = make_offer(merchant="Vernier", variant="wrong", source_url=source.documents[0].source_url,
                           text="25% de descuento con tarjetas de crédito Mastercard Clásica y Oro.", days="jueves y viernes",
                           valid_from=date(2026, 5, 21), valid_until=date(2026, 11, 27))
        with patch("app.scraping.parsers.gnb_html_parser.parse_gnb_pdf", return_value=[wrong]):
            premium, classic, financing = self.parse(source).offers
        self.assertEqual(premium.publication, "pending")
        self.assertIn("gnb_pdf_eligibility_unresolved", source.metadata["parse_warning"])

    def test_legal_same_card_but_different_channel_cannot_confirm_qr(self):
        from unittest.mock import patch
        from app.scraping.schemas import ScrapedSource
        from app.scraping.utils.offers import make_offer
        source = self.source()
        source.documents = [ScrapedSource(source_type="pdf", source_url="https://www.beneficiosbancognb.com.py/imagenes/terms.pdf", text="Condiciones")]
        legal = make_offer(merchant="La Ruta del Café", variant="physical", source_url=source.documents[0].source_url,
                           text="25% de reintegro con tarjetas de crédito Mastercard.", days="de miércoles a domingos",
                           valid_from=date(2026, 7, 1), valid_until=date(2026, 12, 31))
        with patch("app.scraping.parsers.gnb_html_parser.parse_gnb_pdf", return_value=[legal]):
            base, qr = self.parse(source).offers
        self.assertEqual(qr.publication, "pending")
        self.assertIn("gnb_pdf_eligibility_unresolved", source.metadata["parse_warning"])

    def test_adherent_annex_does_not_override_campaign_dates_or_benefits(self):
        from app.scraping.schemas import ScrapedSource
        source = self.source()
        source.documents = [ScrapedSource(source_type="pdf", source_url="https://www.beneficiosbancognb.com.py/imagenes/branches.pdf",
                                          text="Locales adheridos. Del 01 de enero al 31 de diciembre del 2025. Referencia 50% de descuento.",
                                          metadata={"role": "adherents"})]
        offers = self.parse(source).offers
        self.assertTrue(all(o.publication == "confirmed" for o in offers))
        self.assertTrue(any(e.field == "adherents" for e in offers[0].evidence))
        self.assertTrue(any("Locales adheridos" in term for term in offers[0].terms))
        self.assertEqual(offers[0].locations, [])

    def test_api_dates_cannot_repair_shared_year_cross_year_interval(self):
        record = self.record()
        record["startDate"], record["endDate"] = "2026-09-30", "2027-01-29"
        record["description"] = record["description"].replace("Del 01 de julio al 31 de diciembre del 2026", "Del 02 de octubre al 29 de enero del 2027")
        source = self.source(record)
        self.assertTrue(all(o.publication == "pending" for o in self.parse(source).offers))
        self.assertIn("gnb_api_validity_conflict", source.metadata["parse_warning"])

    def test_details_and_heading_with_conflicting_weekdays_stay_pending(self):
        record = self.record()
        record["description"] = record["description"].replace("20% de reintegro para pagos", "20% de reintegro los lunes para pagos")
        source = self.source(record)
        offers = self.parse(source).offers
        self.assertEqual(offers[0].schedule.state, "conflict")
        self.assertTrue(all(o.publication == "pending" for o in offers))

    def test_trailing_weekday_in_combined_heading_applies_to_both_benefits(self):
        record = self.record(620)
        record["description"] = record["description"].replace("Hasta 25% de descuento los jueves y viernes + Hasta 6 cuotas sin intereses todos los días", "Hasta 25% de descuento + Hasta 6 cuotas sin intereses los días sábados")
        offers = self.parse(self.source(record)).offers
        self.assertTrue(all(o.schedule.weekdays == [6] and o.publication == "confirmed" for o in offers))

    def test_unknown_or_missing_api_status_is_pending_and_never_retired(self):
        for status in (None, "true", 1):
            with self.subTest(status=status):
                record = self.record()
                record["status"] = status
                source = self.source(record)
                self.assertTrue(all(o.publication == "pending" for o in self.parse(source).offers))
                self.assertIn("gnb_api_status_unrecognized", source.metadata["parse_warning"])
        record = self.record()
        del record["status"]
        source = self.source(record)
        self.assertTrue(all(o.publication == "pending" for o in self.parse(source).offers))
        self.assertIn("gnb_api_status_unrecognized", source.metadata["parse_warning"])

    def test_qr_bonus_with_different_calendar_cannot_inherit_base_weekdays(self):
        record = self.record()
        record["description"] = record["description"].replace("+5% de reintegro adicional para pagos con QR", "+5% de reintegro adicional los lunes para pagos con QR")
        source = self.source(record)
        base, extra = self.parse(source).offers
        self.assertEqual(base.benefits[0].percentage, 20)
        self.assertEqual(extra.benefits[0].percentage, 5)
        self.assertEqual(extra.publication, "pending")
        self.assertIn("gnb_api_additional_benefit_unresolved", source.metadata["parse_warning"])


def api_page_url(page=0):
    return f"{PUBLIC_API_URL}/benefits?pageSize={API_PAGE_SIZE}&paginationKey={page}"


def api_catalogue(rows, *, page=0, pages=1, total=None):
    return json.dumps({"data": rows, "pagination": {"page": page, "totalPages": pages, "totalElements": len(rows) if total is None else total}})


def api_record(promo_id=175, path="imagenes/terms.pdf"):
    return {"id": promo_id, "title": "Cinemark", "status": True, "description": "<p>50% de descuento los jueves.</p>",
            "termsAndConditionsImage": {"imageLink": f'a:1:{{s:4:"path";s:{len(path)}:"{path}";}}'}}


def api_http(responses):
    return FakeHttp({OFFICIAL_CARDS_URL: "<html>Tarjetas</html>", PORTAL_URL: "<html><app-root></app-root></html>", **responses})


def test_gnb_angular_catalogue_uses_public_api_and_original_list_evidence():
    http = api_http({api_page_url(): api_catalogue([api_record()]), "https://www.beneficiosbancognb.com.py/imagenes/terms.pdf": b"%PDF-original"})
    source = GnbHtmlSource(http).fetch()[0]
    assert source.source_type == "json"
    assert source.source_url == PORTAL_URL + "/categorias/175"
    assert source.metadata["api_url"] == api_page_url()
    assert json.loads(source.text)["data"]["id"] == 175
    assert source.documents[0].content == b"%PDF-original"
    assert source.documents[0].metadata["role"] == "terms"
    assert http.urls == [OFFICIAL_CARDS_URL, PORTAL_URL, api_page_url(), source.metadata["pdf_url"]]


def test_gnb_api_pagination_deduplicates_without_downloads_per_detail():
    http = api_http({api_page_url(): api_catalogue([api_record()], pages=2, total=2),
                    api_page_url(1): api_catalogue([api_record(), api_record(260)], page=1, pages=2, total=2),
                    "https://www.beneficiosbancognb.com.py/imagenes/terms.pdf": b"%PDF-original"})
    sources = GnbHtmlSource(http).fetch()
    assert [source.metadata["promo_id"] for source in sources] == ["175", "260"]
    assert api_page_url(1) in http.urls
    assert not any("/benefits/175" in url for url in http.urls)


@pytest.mark.parametrize("payload", ["<html>Error</html>", json.dumps({"data": {}}), api_catalogue([]),
    api_catalogue([api_record()], pages=2, total=2), api_catalogue([api_record()], page=1),
    api_catalogue([api_record()], pages=0)])
def test_gnb_api_changed_incomplete_or_empty_catalogue_is_not_success(payload):
    http = api_http({api_page_url(): payload, api_page_url(1): api_catalogue([], page=1, pages=2, total=2)})
    with pytest.raises(SourceDiscoveryError):
        GnbHtmlSource(http).fetch()


def test_gnb_api_invalid_pdf_and_unrecognized_descriptor_remain_observable():
    invalid = api_record(260)
    invalid["termsAndConditionsImage"] = {"imageLink": 'O:4:"Evil":0:{}'}
    http = api_http({api_page_url(): api_catalogue([api_record(), invalid]),
                    "https://www.beneficiosbancognb.com.py/imagenes/terms.pdf": b"<html>Access denied</html>"})
    sources = GnbHtmlSource(http).fetch()
    assert [s.metadata["document_error"] for s in sources] == ["invalid_pdf_response", "gnb_unrecognized_legal_file"]
    assert all(not source.documents for source in sources)


def test_gnb_api_limit_marks_partial_coverage(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SCRAPING_MAX_DETAILS_PER_BANK", 1)
    http = api_http({api_page_url(): api_catalogue([api_record(), api_record(260)]),
                    "https://www.beneficiosbancognb.com.py/imagenes/terms.pdf": b"%PDF-original"})
    sources = GnbHtmlSource(http).fetch()
    assert len(sources) == 1 and sources[0].metadata["discovery_warning"] == "gnb_detail_limit_reached"


def test_gnb_factory_default_client_enables_fallback_and_preserves_injection(monkeypatch):
    from app.scraping.factories import get_source
    import app.scraping.sources.gnb_html_source as module
    configurations = []
    class Client:
        def __init__(self, **kwargs):
            configurations.append(kwargs)
    monkeypatch.setattr(module, "HttpClient", Client)
    default = get_source("gnb")
    assert configurations == [{"httpx_fallback_hosts": ("www.beneficiosbancognb.com.py",),
                              "httpx_default_user_agent_on_fallback": True}]
    assert default._owns_client is True
    injected = Client()
    source = get_source("gnb", http_client=injected)
    assert source._http_client is injected and source._owns_client is False


def test_gnb_api_changing_page_count_is_rejected():
    http = api_http({api_page_url(): api_catalogue([api_record()], pages=2, total=2),
                    api_page_url(1): api_catalogue([api_record(260)], page=1, pages=3, total=2)})
    with pytest.raises(SourceDiscoveryError, match="cambió"):
        GnbHtmlSource(http).fetch()


@pytest.mark.parametrize("status", [None, "true", 1])
def test_gnb_api_unknown_publication_state_is_rejected(status):
    record = api_record()
    record["status"] = status
    http = api_http({api_page_url(): api_catalogue([record])})
    with pytest.raises(SourceDiscoveryError, match="estado"):
        GnbHtmlSource(http).fetch()
