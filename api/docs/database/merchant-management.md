# Comercios y locales: operación y próximos pasos

La revisión `20261005_merchant_membership` agrega identidades comerciales, aliases de origen, reglas compartidas y adhesiones a locales. No modifica las migraciones anteriores. El catálogo sirve para cualquier banco; las condiciones siguen separadas por banco y contexto de campaña.

## Qué se guarda

- `merchant_groups`: un comercio estable.
- `merchant_aliases`: referencias publicadas por un banco o revisadas manualmente que apuntan a ese comercio.
- `merchant_locations` y `merchant_location_aliases`: sucursales físicas y sus referencias de origen.
- `merchant_offer_rules`: una regla compartida por observaciones que tienen exactamente las mismas condiciones. Cambios de tarjeta, nivel, porcentaje, tope, fecha, canal o procesadora producen otra regla.
- `promotion_offers`: observaciones originales del scraping, con FK al comercio y a la regla. Se conservan sus IDs, claves, JSON, evidencia, calendario y correcciones.
- `offer_locations`: adhesión de una observación bancaria a un local, con estado y datos de la asociación. No demuestra que ese local esté adherido con otro banco.

Las observaciones históricas conservan su JSON original aunque varias apunten a una sola regla. Esto evita perder evidencia o romper correcciones durante la transición. Las reglas compartidas son inmutables: un cambio crea o reutiliza otra regla y mueve la referencia de la observación.

`location_scope` distingue `specified` —locales identificados—, `all` —declaración explícita de todos los locales— y `unknown`. Un listado vacío nunca significa todos los locales.

La normalización usa aliases exactos dentro de cada banco. Petropar también cuenta con una identidad compartida respaldada por su contrato y anexo. Para otras cadenas, un adaptador puede suministrar `OfferData.merchant` con namespace, clave estable, nombre y evidencia. Los aliases revisados permiten reconocer el mismo comercio entre bancos; no se agrupan marcas por prefijos o similitud de nombres.

En el PDF de supermercados actualmente guardado, Superseis aparece como comercio, pero no hay ciudades o direcciones estructuradas asociadas a esas ofertas. La infraestructura permite presentar sus sucursales cuando una fuente las identifique; no inventa locales ni afirma alcance universal mientras falte esa información.

## Actualizar una instalación existente

En la base de desarrollo de este proyecto la revisión y el backfill ya se aplicaron. Se conservaron las 6.504 observaciones originales; se reconciliaron 947 comercios, 215 locales —incluyendo historia— y 3.394 reglas compartidas. Repetir la operación no produjo cambios. Petropar de octubre mantiene 213 estaciones y 10 variantes en la web.

Guardar un backup de PostgreSQL y conservar el volumen de snapshots. Durante una actualización de esquema, detener temporalmente el scraper y ejecutarlo nuevamente al terminar.

Desde la raíz del backend:

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml stop scraper
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api alembic upgrade head
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api alembic check

# Simular: los cambios se revierten al terminar.
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli normalize-merchants

# Confirmar asociaciones existentes sin descargar páginas ni PDFs.
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli normalize-merchants --apply

docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml --profile scraping up -d scraper
```

El backfill devuelve ofertas examinadas, reconciliadas, alcance desconocido, errores y contadores de cambios. Se puede limitar con `--bank ueno`. Si hay errores, el comando devuelve exit code 1 y revierte la operación completa, incluso con `--apply`. Las simulaciones usan rollback: no persisten filas, aunque PostgreSQL puede consumir valores de secuencias.

Repetirlo sin cambios debe devolver `changes: {}`. La nueva ingesta y las correcciones administrativas mantienen estas relaciones en la misma transacción; no hace falta ejecutar el backfill después de cada scraping. Los locks evitan que el mantenimiento coincida con una actualización del banco.

## Consultar y revisar aliases

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli merchant-list --search Superseis
```

El resultado muestra ID, slug, nombre, bancos, cantidad de locales y aliases. Copiar el namespace y la clave del alias que se desea revisar; seleccionar el slug del comercio de destino.

```text
python -m app.scraping.cli merchant-alias --namespace "bank:itau:merchants" --key "nombre exacto publicado" --merchant-slug "slug-del-comercio-existente" --reason "Referencia cotejada con la fuente oficial" --source-url "https://www.itau.com.py/beneficios"
```

El ejemplo se ejecuta dentro del contenedor API. Sin `--apply` solo simula; para confirmar, añadir `--apply`. El comando guarda URL, motivo y comercio anterior, y reconcilia las observaciones en la misma transacción. No elimina los registros comerciales antiguos. Una asociación manual exige cotejar la fuente: un nombre parecido no basta.

Las URLs anteriores de promociones continúan disponibles. La API agrupa por comercio, banco y contexto de campaña antes de contar y paginar; dentro del detalle conserva los locales y sus variantes. Una campaña con varios comercios mantiene sus ofertas en el detalle original.

## Mejoras recomendadas, por prioridad

1. **Panel de revisión administrativa.** Convertir estos comandos y la administración existente en una pantalla con pendientes, fuentes, aliases, locales y cambios de condiciones. Mostrar el PDF y la oferta juntos simplifica revisar asociaciones.
2. **Fixtures por banco y formato.** Conservar un ejemplo de cada anexo y agregar una prueba cuando aparezca un diseño nuevo. Esto permite corregir parsers con replay sin descargar nuevamente todo el catálogo.
3. **Salud y alertas de actualización.** Exponer en un panel las corridas parciales, fallidas, diferencias de cobertura y antigüedad del último éxito. Configurar notificaciones cuando exista un canal elegido.
4. **Historial de observaciones.** Los snapshots y las correcciones ya se conservan; una tabla de revisiones permitiría comparar cada versión interpretada y explicar un cambio de porcentaje o adhesión directamente desde la UI.
5. **Catálogo de ciudades revisado.** Añadir ciudades y aliases oficiales para filtros consistentes entre bancos. Mantener el texto publicado y no geocodificar o fusionar localidades por coincidencias aproximadas.

No hace falta dividir cada condición bancaria en tablas pequeñas: las identidades y adhesiones se consultan por SQL, y las condiciones variables permanecen en un contrato JSON validado. Las extensiones deben resolver una operación concreta antes de aumentar el esquema.

Referencias: [ADR 006](../adr/006-merchants-and-location-membership.md), [esquema](schema-overview.md), [comandos generales](../../../README.md).
