"""Resources for the opt-in historical category classifier."""
from __future__ import annotations

CATEGORY_SLUGS = [
    "gastronomy",
    "fuel",
    "supermarket",
    "pharmacy",
    "fashion",
    "travel",
    "entertainment",
    "health",
    "home",
    "technology",
    "other",
]

CATEGORIZATION_SCHEMA = {
    "type": "object",
    "properties": {
        "category_slug": {
            "type": "string",
            "enum": CATEGORY_SLUGS,
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
    },
    "required": ["category_slug", "confidence"],
}

_CATEGORY_DESCRIPTIONS = """
- gastronomy: restaurantes, bares, fast food, delivery, heladería, parrilla, sushi, pizza, cafetería, cantina
- fuel: combustibles, estaciones de servicio, nafta, diesel, Shell, Copetrol, Petropar, ENEX
- supermarket: supermercados, hipermercados, almacenes de cadena
- pharmacy: farmacias, droguerías, medicamentos
- fashion: ropa, calzado, indumentaria, joyería, relojería, moda, accesorios
- travel: hoteles, agencias de viaje, turismo, resort, club de golf, marina, hospedaje
- entertainment: cine, teatro, conciertos, espectáculos, entradas, eventos
- health: gimnasios, spa, clínicas, bienestar, fitness, salud
- home: muebles, electrodomésticos, artículos del hogar, decoración, colchones
- technology: electrónica, celulares, computadoras, gadgets, tecnología
- other: todo lo que no encaja en las categorías anteriores
"""


def build_categorization_prompt(title: str, description: str | None) -> str:
    content = f"Título: {title}"
    if description:
        content += f"\nDescripción: {description[:500]}"

    return f"""Sos un clasificador de promociones bancarias de Paraguay.
Clasificá esta promoción en una de las siguientes categorías:
{_CATEGORY_DESCRIPTIONS}
Respondé con el slug exacto de la categoría más apropiada y tu nivel de confianza.
No inventes categorías nuevas.

{content}""".strip()
