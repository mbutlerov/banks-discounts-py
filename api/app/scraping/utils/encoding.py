from __future__ import annotations


def fix_mojibake(text: str) -> str:
    """Corrige texto con mojibake: bytes UTF-8 mal decodificados como Latin-1.

    Convierte cadenas como 'PromociÃ³n' → 'Promoción'.
    Si el texto ya está correcto o no es recuperable, lo devuelve sin cambios.
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
