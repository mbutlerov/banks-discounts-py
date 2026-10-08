"""Historical Sudameris prompt, retained for reference without active consumers."""
from __future__ import annotations

SUDAMERIS_PDF_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "start_date": {"type": ["string", "null"]},
        "end_date": {"type": ["string", "null"]},
        "benefit_type": {
            "type": ["string", "null"],
            "enum": ["discount", "cashback", "installments", "points", "mixed", None],
        },
        "discount_percentage": {"type": ["integer", "null"]},
        "installments": {"type": ["integer", "null"]},
        "applicable_cards": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
    },
    "required": ["title", "start_date", "end_date", "benefit_type",
                 "discount_percentage", "installments", "applicable_cards"],
}

SUDAMERIS_PDF_PROMPT = """
Analizá este PDF de promoción bancaria del Banco Sudameris Paraguay.
Extraé la información y respondé únicamente con JSON válido, sin markdown.

Reglas:
- start_date y end_date en formato YYYY-MM-DD, o null si no se especifica.
- discount_percentage: solo el número entero (ej: 20 para "20% de reintegro"), o null.
- installments: número de cuotas sin interés (ej: 12 para "12 cuotas"), o null.
- benefit_type: "cashback" si hay reintegro, "discount" si hay descuento directo,
  "installments" si solo hay cuotas, "mixed" si combina varios, null si no está claro.
- applicable_cards: lista de tipos de tarjeta mencionados (ej: ["Tarjeta de Crédito", "Mastercard"]),
  o null si no se especifica.
- No inventes datos. Si algo no aparece en el PDF, usá null.
""".strip()
