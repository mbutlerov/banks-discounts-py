from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class UenoPdfDetails:
    description: str | None = None
    discount_percentage: int | None = None
    max_benefit_gs: int | None = None
    installments: int | None = None
    applicable_cards: list[str] = field(default_factory=list)
    raw_text: str = ""


# ---------------------------------------------------------------------------
# Patrones para porcentaje REAL del beneficio
# ---------------------------------------------------------------------------

# "25% de reintegro" / "30% de descuento"  (1-2 dígitos, max 99%)
# Excluye el boilerplate "reintegro del 100% del beneficio"
_EXPLICIT_PERCENT_RE = re.compile(
    r"\b(\d{1,2})%\s+de\s+(?:reintegro|descuento)",
    re.IGNORECASE,
)

# Tabla de niveles: "nivel 5  40%  Gs." o "ueno black  25%  Gs."
_TABLE_PERCENT_RE = re.compile(
    r"(?:nivel\s+\d+|ueno\s+\w+)\s+(\d{1,2})%\s+Gs\.",
    re.IGNORECASE,
)

# Jubilados: "lo cual representa el 50%"
_REPRESENTS_PERCENT_RE = re.compile(
    r"representa\s+el\s+(\d{1,2})%",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Otros patrones
# ---------------------------------------------------------------------------

_INSTALLMENTS_RE = re.compile(
    r"[Hh]asta\s+(\d+)\s+cuotas?\s+sin\s+inter[eé]ses",
)

_CARD_RE = re.compile(
    r"(MasterCard\s+D[uú]o\s+(?:ultra\s+)?(?:Cl[aá]sica|Black|Albirroja)"
    r"|tarjeta\s+de\s+d[eé]bito\s+de\s+ueno\s+bank"
    r"|tarjetas?\s+de\s+d[eé]bito)",
    re.IGNORECASE,
)


def parse_pdf_details(text: str) -> UenoPdfDetails:
    """Extrae campos clave de un PDF individual de promoción de Ueno.

    El texto debe tener el encoding corregido (fix_mojibake aplicado).
    """
    details = UenoPdfDetails(raw_text=text)

    # -- Cuotas sin intereses --------------------------------------------------
    m = _INSTALLMENTS_RE.search(text)
    if m:
        details.installments = int(m.group(1))

    # -- Porcentaje real del beneficio ----------------------------------------
    details.discount_percentage = _extract_real_percentage(text)

    # -- Monto máximo en guaraníes --------------------------------------------
    max_amounts = _extract_max_amounts(text)
    if len(set(max_amounts)) == 1:
        details.max_benefit_gs = max_amounts[0]

    # -- Tarjetas aplicables --------------------------------------------------
    _ws = re.compile(r"\s+")
    cards = list(dict.fromkeys(
        _ws.sub(" ", m.group(0)).strip()
        for m in _CARD_RE.finditer(text)
    ))
    details.applicable_cards = cards

    # -- Descripción corta ----------------------------------------------------
    details.description = _build_description(details)

    return details


def _extract_real_percentage(text: str) -> int | None:
    """Extrae el porcentaje real del beneficio, ignorando el boilerplate legal."""
    candidates: list[int] = []

    # Patrón explícito: "X% de reintegro" o "X% de descuento"
    for m in _EXPLICIT_PERCENT_RE.finditer(text):
        candidates.append(int(m.group(1)))

    # Tabla de niveles (combustibles, etc.): tomar el máximo nivel
    for m in _TABLE_PERCENT_RE.finditer(text):
        candidates.append(int(m.group(1)))

    # Patrón jubilados: "representa el X%"
    for m in _REPRESENTS_PERCENT_RE.finditer(text):
        candidates.append(int(m.group(1)))

    # Scalar compatibility field must never advertise the best tier universally.
    return candidates[0] if len(set(candidates)) == 1 else None


def _extract_max_amounts(text: str) -> list[int]:
    results: list[int] = []
    for pattern in [
        r"REINTEGRO\s+M[AÁ]XIMO[^G]{0,30}Gs\.?\s+([\d.,]+)",
        r"m[aá]ximo[^G]{0,40}Gs\.?\s+([\d.,]+)",
        r"Gs\s+([\d.,]+)\s+como\s+m[aá]ximo",
    ]:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            amount = _parse_gs_amount(m.group(1))
            if amount:
                results.append(amount)
    return results


def _parse_gs_amount(raw: str) -> int | None:
    clean = raw.replace(".", "").replace(",", "")
    try:
        return int(clean)
    except ValueError:
        return None


def _build_description(details: UenoPdfDetails) -> str | None:
    parts: list[str] = []

    if details.discount_percentage:
        parts.append(f"{details.discount_percentage}% de reintegro")

    if details.installments and not details.discount_percentage:
        parts.append(f"Hasta {details.installments} cuotas sin intereses")
    elif details.installments:
        parts.append(f"hasta {details.installments} cuotas sin intereses")

    if details.max_benefit_gs:
        parts.append(f"máximo Gs. {details.max_benefit_gs:,}".replace(",", "."))

    return ". ".join(parts) if parts else None
