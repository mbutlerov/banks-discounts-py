# Fixtures de rutas oficiales

Fragmentos m?nimos capturados por HTTP p?blico el 3 de octubre de 2026:

- `itau_landing.html`: https://www.itau.com.py/beneficios ? enlaces de categor?as ?nicos.
- `itau_category_7.html`: https://www.itau.com.py/beneficios2/categoria/7 ? tarjetas originales de Tatakua y La Queser?a.
- `itau_queseria.html`: https://www.itau.com.py/beneficios2/Detalle?b=17051&c=32058 ? respuesta del detalle, incluidas sus fechas contradictorias.

Los fixtures conservan el HTML del proveedor, sin reconstruir fechas o condiciones. GNB no tiene un fixture de HTML aprobado: la ruta de producci?n devuelve 403 tanto por HTTP como en Edge est?ndar.
