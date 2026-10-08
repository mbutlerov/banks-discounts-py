"""Prompt for the opt-in historical ueno-pdf parser."""
from __future__ import annotations


def build_ueno_page_structuring_prompt(*, page_number: int, page_text: str) -> str:
    return f"""
Devolvé únicamente JSON válido.

Analizá esta página de promociones bancarias.

Si hay promoción, devolvé exactamente:

{{
  "page_number": {page_number},
  "has_promotions": true,
  "items": [
    {{
      "title": "string",
      "description": "string o null",
      "benefit_type": "discount | cashback | installments | points | mixed | null",
      "start_date": "YYYY-MM-DD o null",
      "end_date": "YYYY-MM-DD o null"
    }}
  ]
}}

Si no hay promoción, devolvé exactamente:

{{
  "page_number": {page_number},
  "has_promotions": false,
  "items": []
}}

Reglas:
- No uses markdown.
- No agregues explicación.
- No inventes datos.
- Si la fecha no está clara, usar null.
- Si hay varias promociones, agregalas en items.
- Mantener description breve.

Texto:

{page_text}
""".strip()
