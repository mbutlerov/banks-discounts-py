# Fixtures de rutas oficiales

Fragmentos mínimos capturados por HTTP público el 3 de octubre de 2026:

- `itau_landing.html`: https://www.itau.com.py/beneficios — enlaces de categorías únicos.
- `itau_category_7.html`: https://www.itau.com.py/beneficios2/categoria/7 — tarjetas originales de Tatakua y La Quesería.
- `itau_queseria.html`: https://www.itau.com.py/beneficios2/Detalle?b=17051&c=32058 — respuesta del detalle, incluidas sus fechas contradictorias.

Los fixtures conservan la estructura y condiciones del proveedor, normalizando
saltos de línea y espacios finales. No reconstruyen fechas ni beneficios.
GNB no utiliza estos fixtures HTML: su adaptador consume la API pública del
portal y tiene pruebas propias en `tests/banks/test_gnb.py`.
