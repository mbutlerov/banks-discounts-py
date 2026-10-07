from __future__ import annotations

import re

# (slug, keywords) — orden importa: más específico primero
_RULES: list[tuple[str, list[str]]] = [
    ("fuel", [
        "shell", "copetrol", "copemarket", "enex", "petropar", "combustible",
        "nafta", "diesel", "gasolinera", "estacion de servicio", "estaciones de servicio",
        "petro ", "ypf",
    ]),
    ("pharmacy", [
        "farmacenter", "farmacia", "farma", "catedral", "droguería",
    ]),
    ("supermarket", [
        "supermercado", "super 6", "superseis", "real supermercado", "stock",
        "hipermercado", "disco ", "carrefour",
    ]),
    ("gastronomy", [
        "biggie", "pizza", "sushi", "burger", "bacon", "monchis", "heladería",
        "heladeria", "parrilla", "asadero", "restaurante", "gastronomía", "gastronomia",
        "food", "kitchen", "chef", "cafetería", "cafeteria", "café", "café", "bar ",
        "boliche", "bares", "cantina", "comida", "delivery", "dino ", "juan valdez",
        "frigomas", "familia paru", "lorelet", "serendipity", "hornalla",
        "colegios", "saspy", "cook", "grill", "ceviche", "mariscos",
    ]),
    ("travel", [
        "travel", "turismo", "hotel", "resort", "viaje", "agencia de viaje",
        "hospedaje", "posada", "alojamiento", "yacht", "golf club", "tour",
    ]),
    ("entertainment", [
        "cine", "teatro", "entrada", "korn", "concierto", "concert",
        "entretenimiento", "espectáculo", "espectaculo", "virtuality",
    ]),
    ("health", [
        "gym", "fitness", "bienestar", "salud", "spa", "wellness",
        "live fitness", "médico", "medico", "clínica", "clinica",
    ]),
    ("fashion", [
        "sax ", " sax", "lacoste", "benetton", "pandora", "montblanc",
        "furla", "benetton", "uza ", "la cave", "armele", "varsovia",
        "ropa", "indumentaria", "calzado", "moda", "fashion", "boutique",
        "joyería", "joyeria", "relojería", "relojeria", "vendome",
    ]),
    ("technology", [
        "tecnología", "tecnologia", "celular", "samsung", "iphone",
        "electrónica", "electronica", "computadora", "laptop", "pc ",
    ]),
    ("home", [
        "ashley", "lincoln", "casa ", "hogar", "muebles", "decoración",
        "decoracion", "colchon", "tramontina", "frigidaire", "electrodomestico",
    ]),
]


# Mapeo de nombres de categorías de Itaú a nuestros slugs
_ITAU_CATEGORY_MAP: dict[str, str] = {
    "gastronomía": "gastronomy",
    "supermercados": "supermarket",
    "indumentaria": "fashion",
    "tecnología": "technology",
    "hogar": "home",
    "belleza y salud": "health",
    "spa": "health",
    "viaje y turismo": "travel",
    "entretenimiento": "entertainment",
    "teatro": "entertainment",
    "niños": "entertainment",
    "american express": None,   # tipo de tarjeta, no categoría
    "mastercard debit": None,
}


def categorize(
    title: str,
    description: str | None = None,
    category_hint: str | None = None,
) -> str:
    """Devuelve el slug de categoría que mejor corresponde.

    1. Si hay un hint (ej. nombre de categoría de Itaú), lo usa primero.
    2. Si no, aplica keyword matching sobre título + descripción.
    3. Fallback: 'other'.
    """
    # Hint externo (ej. Itaú category_name)
    if category_hint:
        mapped = _ITAU_CATEGORY_MAP.get(category_hint.lower().strip())
        if mapped:
            return mapped

    text = f"{title} {description or ''}".lower()
    # Normalizar tildes para matching más robusto
    text = _strip_accents(text)

    for slug, keywords in _RULES:
        for kw in keywords:
            kw_norm = _strip_accents(kw.lower())
            # \b solo al inicio: "restaurante" matchea "restaurantes", "farma" matchea "farmacias"
            if re.search(r"\b" + re.escape(kw_norm.strip()), text):
                return slug

    return "other"


def _strip_accents(text: str) -> str:
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "à": "a", "è": "e", "ì": "i", "ò": "o", "ù": "u",
        "ä": "a", "ë": "e", "ï": "i", "ö": "o", "ü": "u",
        "ñ": "n",
    }
    for accented, plain in replacements.items():
        text = text.replace(accented, plain)
    return text
