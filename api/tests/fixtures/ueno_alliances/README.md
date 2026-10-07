# Catálogo mensual y bases legales de Ueno

Captura: **03/10/2026**, exclusivamente fuentes oficiales. Cada JSON registra
`source_url`, `detail_url` cuando corresponde, y SHA-256 del PDF original.
Se conservan campos y tablas necesarias para pruebas; no logos ni PDFs completos.
Los casos `pending-*` se agregaron el **05/10/2026** a partir de snapshots
previamente almacenados, con fecha de captura y páginas originales. Documentan
los patrones incorporados en `canonical-v3` y sus límites de interpretación.

La fuente principal es [Alianzas Ueno](https://www.ueno.com.py/alianzas-ueno/).
El [PDF de octubre](https://www.ueno.com.py/wp-content/uploads/2026/10/BENEFICIOS-ueno-octubre2026.pdf)
tiene 25 páginas, 11.933.372 bytes y SHA-256
`03eb8ed60aa0d24b0c412dad9522e969c6f1f449a40c7fcd856135547b93f196`.
Sus anotaciones contienen **27 destinos legales únicos**. `catalogue.json`
registra todos los enlaces y su página, incluyendo destinos externos que el
scraper descarta. Las pruebas construyen un PDF pequeño con esas anotaciones;
no descargan el documento bancario.

El flyer es un índice. Los nombres en logos y el texto de columnas superpuestas
no sirven para asignar descuentos a comercios. La adquisición sigue cada enlace
`/beneficio-byc/.../`, obtiene su PDF legal y conserva la procedencia del flyer
(URL, hash y página). [Información legal](https://www.ueno.com.py/informacion-legal/)
complementa esa lista; sus pestañas y acordeones ya están en el HTML del servidor.
Los enlaces repetidos se descargan una sola vez y tienen prioridad los publicados
en el flyer actual. El mes de la URL sirve para ordenar descubrimiento.

Los fixtures prueban la tabla de niveles y sus topes junto con el anexo de
comercios, las direcciones de sucursales y las redes de pago, sin cruzar columnas
entre anexos. `cuotas-sin-intereses.json` conserva la página 1 contractual y el
anexo completo: **135 comercios únicos**, hasta 12 cuotas, con evidencia de las
páginas 2–6. Los demás anexos conservan filas representativas.

Casos que necesitan interpretación separada:

- Entretenimiento: anexo I tiene reintegro para Flightnex y Virtuality;
  anexo II tiene cuotas para Gorillaz, Ed Sheeran y Camilo. No son combinables
  por el hecho de compartir un PDF. La preventa de Camilo tiene inicio sin año
  y queda pendiente.
- Farmacias: anexo I contiene comercio/red/vigencia y anexo II contiene
  comercio/dirección/red. La dirección pertenece a `locations`.
- Petropar: las sucursales del POS y de la app tienen identidades con nombre,
  dirección, ciudad, red y canal. Filas idénticas no duplican variantes.
  `petropar-current.json` registra el PDF v2 consultado el **05/10/2026**,
  incluyendo todo el anexo: **191 adhesiones POS y 23 de la App Petropar**.
  La nueva fila respecto de v1 es Petropar Punto 63, Piribebuy, página 11.
  Se conserva una ubicación estructurada por fila con su evidencia; los cinco
  niveles comparten esa ubicación y las claves previas de las ofertas siguen
  iguales. Una fila POS no habilita la app: la adhesión por canal se consulta
  en su propio anexo. La app no declara una red y no hereda Upay del POS.
  Asunción y Luque se leen de `Zona`, no del nombre.
- Club Internacional de Tenis: vigencia propia **01/09/2025–26/08/2029**;
  los conceptos de Upay y del débito automático Infonet se mantienen separados.
- ubox: vigencia propia **01/09/2026–31/12/2026**. La matriz Black suma 20% base
  y 30% adicional con tope total de Gs. 63.500; no usa el tope parcial de Gs. 38.100.
- Koala: descuento comercial 45% y reintegro bancario 10% sobre importe neto;
  no se publica un supuesto 55%.
- Feria Palmear: sólo sábados. La vigencia mensual no reemplaza ese calendario.
- Deportes: se conocen comercios, niveles y topes, pero falta una regla explícita
  de compra interpretable; las variantes permanecen pendientes. La acumulación
  del tope durante una campaña no es evidencia de compra todos los días.

Regresiones de la revisión de pendientes:

- `pending-day-children.json`: reconoce la aplicación durante la vigencia
  según detalle, conservando la personalización por nivel y saldo promedio.
- `pending-kingo.json`: dos fechas de compra con un año compartido; se conserva
  cada fecha y se verifica que su día de semana coincida.
- `pending-black-wellness.json`, `pending-black-della.json` y
  `pending-black-free-spirit.json`: matrices Black de cuatro columnas y comercios
  adheridos. El calendario de compra y el medio de pago vienen del contrato.
- `pending-black-shops-cda.json`: matriz de cinco columnas con condiciones CDA,
  límites por tarjeta y anexo de tiendas. Las cuotas forman ofertas separadas:
  no heredan los topes de reintegro ni la condición CDA.
- `pending-black-cuadrita.json`: los días de compra miércoles a sábado
  prevalecen sobre una mención general a recibir el beneficio durante la vigencia.
- `pending-cuadrita-conflict.json`, `pending-power-invalid.json` y
  `pending-sports.json`: conflicto entre contrato y anexo, fecha imposible de
  septiembre y calendario ausente. Los tres siguen pendientes; la vigencia
  de una fila no repara una vigencia contractual contradictoria o inválida.

Una tabla ilegible, encabezados desconocidos, fechas sin año o un anexo ausente
no permiten publicar beneficios universales ni retirar variantes previas por
ausencia. Los parsers trabajan con bytes/celdas guardados y no acceden a Internet.
