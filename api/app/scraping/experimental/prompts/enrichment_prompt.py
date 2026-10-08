"""Resources for the opt-in historical enrichment job."""
from __future__ import annotations

ENRICHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "discount_percentage": {"type": ["integer", "null"]},
        "start_date": {"type": ["string", "null"]},
        "end_date": {"type": ["string", "null"]},
        "installments": {"type": ["integer", "null"]},
        "applicable_cards": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
    },
    "required": ["discount_percentage", "start_date", "end_date", "installments", "applicable_cards"],
}


def build_enrichment_prompt(
    *,
    title: str,
    benefit_type: str | None,
    missing_fields: list[str],
    content: str,
) -> str:
    missing_str = ", ".join(missing_fields)
    return f"""
Tenés una promoción bancaria de Paraguay con los siguientes datos actuales:
- Título: {title}
- Tipo de beneficio: {benefit_type or "desconocido"}
- Campos que faltan: {missing_str}

Usando el siguiente contenido de la página/PDF de la promoción, completá SOLO los campos faltantes.
Si un campo no se puede determinar con certeza desde el contenido, usá null.
No inventes datos.

Reglas:
- start_date y end_date: formato YYYY-MM-DD, o null.
- discount_percentage: número entero (ej: 20 para "20% de reintegro"), o null.
  No uses 100 aunque el texto diga "100% del beneficio" — eso es lenguaje legal, no el porcentaje real.
- installments: cantidad de cuotas sin interés, o null.
- applicable_cards: lista de tipos de tarjeta mencionados, o null.

Contenido:
{content[:3000]}
""".strip()
