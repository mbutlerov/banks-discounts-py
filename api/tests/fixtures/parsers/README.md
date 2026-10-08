# Fixtures de parsers

Capturas de lectura del **03/10/2026**, ampliadas el **05/10/2026** con los
documentos guardados al revisar pendientes y el **06/10/2026** con registros
de la API pública GNB. Son campos públicos y celdas de
tablas; algunos HTML conservan la estructura funcional de la página de detalle.
No se versionan PDFs completos ni activos binarios del banco. Los fragmentos
HTML normalizan saltos de línea y espacios al final de cada línea; los hashes
que aparecen en sus comentarios identifican el snapshot original, no el fixture.
Los snapshots operativos conservan los bytes originales fuera de Git.
Las filas JSON son resultados reales de `pdfplumber.extract_tables`; `None`
representa una celda fusionada. La suite funciona sin red y sin base de datos.

- `itau_tatakua.html`: https://www.itau.com.py/beneficios2/Detalle?b=17050&c=30789
  (se quitaron estilos/imágenes; fechas contradictorias y variantes originales).
- `sudameris_plub.html`: https://www.sudameris.com.py/beneficios/destacado/360/detalle
- `sudameris_biggie.html`: https://www.sudameris.com.py/beneficios/destacado/891/detalle
- `atlas_biggie*`: https://www.bancoatlas.com.py/web/beneficios
  (atributos funcionales, extracto mínimo de vigencia y JSON-LD del mismo comercio).
- `atlas_dated_events.json`: atributos de tarjetas oficiales y JSON-LD de
  vigencia para promociones de fecha única o intervalo. El año del evento
  se toma de una vigencia oficial compatible; una contradicción sigue pendiente.
- `itau_pending_wallet_hering.html` e `itau_pending_wallet_indio.html`:
  detalles oficiales con HTML suficiente para probar discovery y parsing de
  beneficio base, adicional de billetera y cuotas. El comentario inicial
  registra URL y hash del snapshot; los activos enlazados no se descargan.
- `itau_pending_card_context_origen.html`: tarjetas exigidas por el beneficio
  frente a las menciones de programas de puntos; no se heredan tarjetas de
  texto ajeno a la condición de compra.
- `itau_pending_benefit_conflict_bertoni.html` e
  `itau_pending_missing_schedule_syrocco.html`: contradicciones de porcentajes
  y ausencia de calendario explícito. Permanecen pendientes en la prueba.
- `ueno_power.json`, `ueno_fuel.json`, `sudameris_gastronomy.json`: URL oficial
  y página de cada tabla están dentro del archivo. Se conservaron pocas filas
  con días, porcentajes, topes y tarjetas; el texto de vigencia fue normalizado.
- `ueno_coupon*`, `ueno_combo*`, `sudameris_farmacenter*` y
  `sudameris_tatano*`: campos y extractos de snapshots ya adquiridos por el
  runner, sin nuevas descargas. Ver URL de origen en cada JSON. Cubren
  personalización, cupón por importe fijo, combo con vigencias propias,
  descuento + reintegro + cuotas y cuotas restringidas a alojamiento.
- `sudameris_zona_central_columns.json` y
  `sudameris_zona_este_columns.json`: catálogos regionales con cinco campos
  semánticos y columnas físicas vacías o encabezados en varias filas.
  Conservan filas representativas y sus páginas para separar descuento,
  reintegro, cuotas, topes y vigencia por comercio.
- `sudameris_ccu_annex.json` y `sudameris_enex_annex.json`: anexos de locales
  con ciudad, dirección y canal. Las estaciones de la app y del POS conservan
  adhesiones distintas; la dirección no se interpreta como un porcentaje.
- `sudameris_live_fitness.json`: condiciones por plan de membresía y cuotas,
  sin combinar requisitos de planes distintos. Los JSON de esta ampliación
  registran URL oficial, fecha de captura y páginas originales.
- `gnb_popeyes.txt`: extracto mínimo normalizado de
  https://www.beneficiosbancognb.com.py/imagenes/5009_bases-y-condiciones-popeyes.pdf
  visible en el índice público. **Venció en junio de 2026:** solo es un fixture,
  no se usa como fallback vigente. El portal v2 respondió HTTP 403 a la lectura
  inicial con Requests; el acceso al catálogo público de producción con HTTPX
  se verificó el 6 de octubre. El adaptador no consulta mirrors de QA.
- `gnb_api_details.json`: campos públicos mínimos del listado oficial
  https://www.beneficiosbancognb.com.py/v2/apis/rewards/rewards/v1/benefits/benefits?pageSize=999&paginationKey=0
  capturado el **06/10/2026**. Conserva identificación, título, fechas,
  descripción HTML y enlaces de casos representativos: Cinemark, La Ruta
  Gastronómica, La Ruta del Café, Casa Rica y otras variantes. Permite probar
  tarjetas/productos, adicional QR, cuotas, sucursales y contradicciones de
  fechas. Los casos con conflicto no se transforman en promociones confirmadas.

Las variaciones sintéticas sobre esos registros son regresiones explícitas de
topes ambiguos, PDF contradictorio, estados desconocidos y calendarios sin
resolver; no representan nuevas capturas del banco. Las pruebas de adquisición
usan respuestas controladas de paginación y PDF, sin guardar documentos completos.

Los mocks HTML de discovery en las pruebas son casos controlados de contrato,
no capturas bancarias. No acreditan que un selector nuevo funcione en producción.
