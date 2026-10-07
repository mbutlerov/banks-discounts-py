"""Regressions for benefit clauses observed in the official pending catalog."""
from decimal import Decimal

from app.scraping.utils.offers import extract_benefits, extract_eligibility


def test_sudameris_materassi_mixed_discount_and_cashback_are_associated():
    benefits = extract_benefits("30% de descuento en caja + 10% de reintegro Hasta 12 cuotas sin intereses")
    assert [(b.type, b.percentage) for b in benefits if b.percentage is not None] == [
        ("discount", Decimal("30")), ("cashback", Decimal("10")),
    ]
    assert next(b for b in benefits if b.type == "installments").installments == 12


def test_next_percentage_does_not_assign_its_kind_to_the_previous_one():
    benefits = extract_benefits("30% en caja + 10% de reintegro", default_type="discount")
    assert [(b.type, b.percentage) for b in benefits] == [
        ("discount", Decimal("30")), ("cashback", Decimal("10")),
    ]
    benefits = extract_benefits("30% en caja + 10% de reintegro")
    assert [(b.type, b.percentage) for b in benefits] == [("cashback", Decimal("10"))]


def test_prefix_descriptors_bind_to_their_own_percentages():
    benefits = extract_benefits("descuento 30% en caja, reintegro 10%", default_type="cashback")
    assert [(b.type, b.percentage) for b in benefits] == [
        ("discount", Decimal("30")), ("cashback", Decimal("10")),
    ]


def test_financing_rates_and_surcharges_do_not_inherit_a_discount():
    for text in [
        "20% de descuento. Recargo del 5% por financiación.",
        "30% de descuento y 10% de interés mensual",
        "20% de descuento; tasa nominal anual del 5%",
        "20% de descuento y recargo del 5% por financiación",
    ]:
        for default in [None, "cashback"]:
            benefits = extract_benefits(text, default_type=default)
            assert len(benefits) == 1
            assert (benefits[0].type, benefits[0].percentage) == ("discount", Decimal(text[:2]))
    assert extract_benefits("Reintegro disponible. Otra condición del 5%.") == []
    assert extract_benefits("20% de descuento y 12 cuotas sin intereses")[0].percentage == Decimal("20")
    benefits = extract_benefits("20% de ahorro + 5% VISA y hasta 6 cuotas sin intereses.", default_type="discount")
    assert [(b.type, b.percentage) for b in benefits if b.percentage is not None] == [
        ("discount", Decimal("20")), ("discount", Decimal("5")),
    ]


def test_sudameris_payment_bullets_after_web_exclusion_are_preserved():
    text = (
        "• Válido únicamente en el local de Paseo Miraflores. No aplica a página web\n"
        "• Aplica a compras realizadas con tarjetas de crédito MasterCard y Visa de Sudameris Bank S.A.E.C.A.\n"
        "• Promoción válida única y exclusivamente para compras realizadas vía POS de Bancard (Red Infonet)."
    )
    eligibility = extract_eligibility(text)
    assert set(eligibility.cards) == {"Mastercard", "Visa"}
    assert eligibility.card_types == ["credit"]
    assert eligibility.channels == ["POS"]
    assert set(eligibility.processors) == {"Bancard", "Infonet"}
    assert "cards" not in eligibility.unknown_fields
    assert eligibility.conditions == [text]


def test_excluded_cards_and_channels_are_not_eligible_even_with_later_positive_clause():
    eligibility = extract_eligibility(
        "Aplica con Visa Infinite. Se excluyen Mastercard Black y tarjetas de débito. "
        "No aplica a pagos QR o web. Aplica exclusivamente mediante POS."
    )
    assert eligibility.cards == ["Visa Infinite"]
    assert eligibility.card_types == []
    assert eligibility.channels == ["POS"]
