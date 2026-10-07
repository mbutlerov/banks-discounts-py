"""Purchase calendar extraction, independent of any bank adapter."""
import unittest
from datetime import date

from app.scraping.utils.days import extract_valid_days
from app.scraping.utils.schedule import extract_schedule
from app.scraping.utils.offers import extract_validity, make_offer

class CalendarExtractionTests(unittest.TestCase):
    def test_unknown_and_posting_days_never_become_purchase_days(self):
        self.assertEqual(extract_schedule("Beneficio sujeto a condiciones").state, "unknown")
        self.assertEqual(extract_schedule("Acreditación de lunes a viernes").state, "unknown")
        rule = extract_schedule("Aplica los domingos. La acreditación ocurre de lunes a viernes.")
        self.assertEqual(rule.weekdays, [7])

    def test_exclusion_wraparound_and_weekend(self):
        self.assertEqual(extract_valid_days("Todos los días excepto lunes"),
                         ["tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])
        self.assertEqual(extract_schedule("Viernes a lunes").weekdays, [1, 5, 6, 7])
        self.assertEqual(extract_schedule("Fin de semana").weekdays, [6, 7])

    def test_monthly_rules_do_not_project_to_all_fridays(self):
        rule = extract_schedule("Último viernes de cada mes")
        self.assertEqual((rule.kind, rule.ordinal, rule.weekdays), ("monthly_nth_weekday", -1, [5]))
        self.assertIsNone(extract_valid_days("Último viernes de cada mes"))
        self.assertEqual(extract_schedule("01 de cada mes").month_days, [1])

    def test_one_weekday_and_yearless_event_is_unknown(self):
        self.assertEqual(extract_schedule("Promoción válida el jueves 22 de diciembre").state, "unknown")
        for text in ["Solo el viernes 3 de octubre", "viernes 3 octubre", "viernes 3/10"]:
            self.assertEqual(extract_schedule(text, dedicated=True).state, "unknown")
        self.assertEqual(extract_schedule("Promoción válida el viernes 31 de septiembre de 2026").state, "conflict")

    def test_numeric_purchase_list_does_not_drop_impossible_dates(self):
        for text in [
            "Exclusivamente el sábado 03/10 y sábado 17/10/2026",
            "03.10 y 17.10.2026",
            "03/10/26 y 17/10/2026",
        ]:
            self.assertEqual(extract_schedule(text, dedicated=True).state, "unknown")
        for text in [
            "Exclusivamente el viernes 31/09/2026 y sábado 17/10/2026",
            "31.09.2026 y 17.10.2026",
            "2026-09-31 y 2026-10-17",
            "viernes 31/09/2026",
        ]:
            self.assertEqual(extract_schedule(text, dedicated=True).state, "conflict")
        offer = make_offer(merchant="Ejemplo", variant="base", source_url="https://example.com",
                           text="20% de descuento", days="31/09/2026 y 17/10/2026",
                           valid_from=date(2026, 9, 1), valid_until=date(2026, 10, 31))
        self.assertEqual((offer.publication, offer.schedule.state), ("pending", "conflict"))

    def test_monthly_range_does_not_collapse_to_its_last_day(self):
        rule = extract_schedule("Aplica del 1 al 10 de cada mes")
        self.assertEqual((rule.kind, rule.month_days), ("monthly_day", list(range(1, 11))))

    def test_multiple_ordinals_and_contradictory_explicit_dates_need_review(self):
        self.assertEqual(extract_schedule("Primer y tercer lunes de cada mes").state, "unknown")
        self.assertEqual(extract_schedule("Promoción válida el viernes 3 de octubre de 2026").state, "conflict")

    def test_kingo_date_list_keeps_both_saturdays_with_shared_contractual_year(self):
        text = "Exclusivamente los días sábado 03 de octubre y sábado 17 de octubre 2026."
        rule = extract_schedule(text, dedicated=True)
        self.assertEqual((rule.state, rule.kind, rule.dates),
                         ("known", "specific_dates", [date(2026, 10, 3), date(2026, 10, 17)]))
        offer = make_offer(merchant="Kingo", variant="nivel 1", source_url="https://www.ueno.com.py/example.pdf",
                           text=text + " 20% de reintegro con tarjeta de crédito.", days=text)
        self.assertEqual((offer.valid_from, offer.valid_until, offer.publication),
                         (date(2026, 10, 3), date(2026, 10, 17), "confirmed"))

    def test_date_list_requires_year_and_checks_each_weekday(self):
        self.assertEqual(extract_schedule("Exclusivamente los días sábado 03 de octubre y sábado 17 de octubre").state, "unknown")
        self.assertEqual(extract_schedule("Exclusivamente los días viernes 03 de octubre y sábado 17 de octubre 2026").state, "conflict")
        self.assertEqual(extract_schedule("Exclusivamente el 31 de septiembre y el 17 de octubre 2026").state, "conflict")
        self.assertEqual(extract_schedule("Exclusivamente los días 31 de septiembre y 17 de octubre 2026").state, "conflict")
        # No shared year can be chosen across ambiguous year transitions.
        self.assertEqual(extract_schedule("Exclusivamente los días 31 de diciembre 2026, 1 de enero y 2 de enero 2027").state, "unknown")
        self.assertEqual(extract_schedule("Exclusivamente el 31 de diciembre y el 1 de enero de 2027").state, "unknown")
        self.assertEqual(extract_schedule("Exclusivamente los días 3 y 17 de octubre 2026").state, "unknown")
        self.assertEqual(extract_schedule("Exclusivamente el 3 de octubre o el 17 de octubre de 2026").state, "unknown")
        self.assertEqual(extract_schedule("Exclusivamente el viernes 03/10/2026 y sábado 17/10/2026").state, "conflict")

    def test_explicit_one_day_with_intraday_hours(self):
        rule = extract_schedule("Exclusivamente el 19 de septiembre del 2026 desde las 00:00h hasta las 23:59h.")
        self.assertEqual((rule.kind, rule.dates), ("specific_dates", [date(2026, 9, 19)]))
        self.assertEqual(extract_schedule("Desde el 01 de octubre hasta el 31 de octubre del 2026.", dedicated=True).state, "unknown")
        self.assertEqual(extract_schedule("La acreditación ocurre el 19 de septiembre de 2026 desde las 00:00h hasta las 23:59h.").state, "unknown")
        self.assertEqual(extract_schedule("Exclusivamente el 19 de septiembre del 2026 desde las 23:59h hasta las 00:00h.").state, "unknown")

    def test_contractual_ranges_allow_missing_de_and_pdf_linebreaks(self):
        self.assertEqual(extract_validity("Del 5 al 18 octubre 2026."), (date(2026, 10, 5), date(2026, 10, 18)))
        self.assertEqual(extract_validity("Del martes 6 de enero al 1 de diciembre de 2026."),
                         (date(2026, 1, 6), date(2026, 12, 1)))
        self.assertEqual(extract_validity("Exclusivamente del 14 septiembre del 2026 al 20 de septiembre del 2026"),
                         (date(2026, 9, 14), date(2026, 9, 20)))
        self.assertEqual(extract_validity("Desde el 06 agosto hasta el 07 de agosto del 2026."),
                         (date(2026, 8, 6), date(2026, 8, 7)))
        self.assertEqual(extract_validity("Desde el 01.07.26 Hasta el 31.12.26"), (date(2026, 7, 1), date(2026, 12, 31)))
        self.assertEqual(extract_validity("Del 01 de setiembre hasta el 31 de octubre 2026"),
                         (date(2026, 9, 1), date(2026, 10, 31)))
        self.assertEqual(extract_validity("Del 5 al 18 octubre"), (None, None))
        self.assertEqual(extract_validity("Desde el 01 de septiembre hasta el 31 de septiembre del 2026."), (None, None))
