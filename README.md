# Banks Discounts

Catálogo de descuentos bancarios de Paraguay: FastAPI + PostgreSQL, con la web Next.js en el repositorio hermano `banks-discounts-web`.

La web consulta ofertas ya guardadas. El scraping ocurre fuera de la navegación, con un adaptador para **Ueno, Sudameris, Itaú, Atlas y GNB**. Cada comercio conserva sus variantes de tarjeta/nivel, calendario, vigencia, topes, requisitos y evidencia. Los datos incompletos se muestran solamente al activar pendientes.

Los parsers principales son deterministas. Las propuestas con IA y el OCR se
ejecutan explícitamente para revisión; no publican automáticamente condiciones
que no pudieron confirmarse. La [arquitectura de scraping](api/app/scraping/scraping_architecture.md)
describe el recorrido específico de cada banco y la evidencia conservada.

## Arrancar en Windows

Requisitos: Docker Desktop con Compose reciente. Para la web: Node.js 20.9+ y npm; se recomienda una versión actual con datos de zona horaria recientes.

Desde este repositorio:

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build api db
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api alembic upgrade head
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.database.seed
```

Las migraciones son aditivas y el seed es idempotente. Antes de actualizar una base que contiene información útil, guardar un `pg_dump`. Se probaron una base vacía, el esquema anterior sin historial y el esquema con revisión `b8a7ff77da5a`. El downgrade por debajo de la base histórica se bloquea para proteger las tablas adoptadas.

Crear `api/environments/private.env` a partir de [private.env.example](api/environments/private.env.example) y reemplazar `ADMIN_API_KEY` por una clave propia. Este archivo está ignorado por Git y excluido del build Docker. La API pública funciona sin clave; la administración permanece deshabilitada si no hay clave configurada. Tras cambiar variables de entorno, recrear API y worker con `up -d`.

En el repositorio web:

```powershell
cd ../banks-discounts-web
npm.cmd ci
# Crear .env.local con API_BASE_URL=http://localhost:8000/api/v1
npm.cmd run dev
```

Accesos: [web](http://localhost:3000), [Swagger](http://localhost:8000/docs), [health](http://localhost:8000/api/v1/health). PostgreSQL de desarrollo escucha en `localhost:5432`.

Los puertos de API y PostgreSQL del entorno de desarrollo se publican solamente
en `127.0.0.1`. Para una prueba de la web desde esta computadora, ejecutar
`npm.cmd run dev -- --hostname 127.0.0.1` en el repositorio frontend.
Una URL publicada en Internet necesita un control de acceso para limitarla a su
propietario; la web actual no tiene un login de visitantes.

## Comercios, sucursales y reglas compartidas

La migración `20261005_merchant_membership` conecta las ofertas con comercios y locales estables. Conserva las observaciones originales y comparte reglas idénticas; permite una tarjeta por comercio/banco/campaña y consultar sus sucursales sin mezclar condiciones.

Para una base ya poblada, después de `alembic upgrade head`:

```powershell
# Simulación con rollback.
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli normalize-merchants

# Aplicar el backfill; las nuevas corridas lo mantienen automáticamente.
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli normalize-merchants --apply

# Consultar identidades y aliases para revisar asociaciones.
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli merchant-list --search Superseis
```

Ver [operación de comercios y recomendaciones](api/docs/database/merchant-management.md) para aliases revisados, alcance de locales, actualización segura y próximos pasos.

## Poblar y actualizar descuentos

Ejecutar un banco o todos:

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli run --bank itau
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli run --bank all
```

`--bank` acepta `ueno`, `sudameris`, `itau`, `atlas`, `gnb`, `all`. Una corrida fallida devuelve exit code 1; una parcial publica las ofertas válidas y conserva pendientes con sus motivos. GNB obtiene su catálogo desde la API pública que utiliza el portal de producción y descarga sus bases PDF. Ante un HTTP 403 de Requests en el dominio exacto de beneficios GNB, reintenta con HTTPX y su User-Agent estándar. Las fechas o condiciones contradictorias quedan pendientes; los fallos de acceso se registran y conservan los datos anteriores.

Activar el proceso periódico independiente:

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml --profile scraping up -d --build scraper
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml logs --tail 30 scraper
```

Este worker procesa la cola administrativa y revisa cuándo corresponde actualizar cada banco. El intervalo por defecto es un día; reiniciarlo conserva la fecha de la última corrida en PostgreSQL. Los locks impiden dos corridas simultáneas del mismo banco. API y worker comparten el volumen de snapshots.

Detener servicios conservando sus volúmenes:

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml --profile scraping down
```

## Consultar la API

```text
GET /api/v1/promotions?date=2026-10-03
GET /api/v1/promotions?date_from=2026-10-03&date_to=2026-10-04&bank_slug=ueno&bank_slug=itau
GET /api/v1/promotions?date=2026-10-03&include_pending=true
GET /api/v1/promotions/{slug}?date_from=2026-10-03&date_to=2026-10-04
GET /api/v1/catalogs/banks
GET /api/v1/catalogs/categories
```

Rangos de hasta 31 días, fechas locales de Paraguay. Filtros: banco repetido o separado por coma, `category_slug`, `search`, `benefit_type`, `min_discount`, `max_discount`, `card`, `level`, `channel`, `page`, `size` (máximo 100). Tarjeta, nivel y canal se comparan con los nombres canónicos exactos. `day` anterior sigue disponible; sin fecha selecciona la siguiente ocurrencia del día elegido.

La respuesta incluye `matched_dates` y `variants`. Todos los filtros deben coincidir en una misma variante; el tipo y porcentaje deben pertenecer al mismo beneficio. El total se calcula después de aplicar disponibilidad y agrupar, antes de paginar. `include_pending=true` agrega ofertas sin confirmar, con `availability=unknown|conflict`; no se asignan fechas de coincidencia a esos casos.

No se interpreta vigencia desconocida como infinita, ni días ausentes como todos los días. El calendario materializado cubre 90 días y puede regenerarse; fuera de esa cobertura o con una versión desactualizada se usa el evaluador exacto.

El catálogo de bancos informa `data_status` (`updated`, `partial`, `unavailable`, `never`), `last_attempt_at` y `last_updated_at`. La web usa estos datos para distinguir un intervalo sin coincidencias de un banco cuya fuente todavía no se pudo actualizar. Los mensajes públicos no contienen errores internos del scraper.

## Administración, evidencia y replay

Swagger permite **Authorize** con `ADMIN_API_KEY` como bearer. Rutas autenticadas:

```text
POST /api/v1/admin/scrape-runs                  {"bank_slug":"atlas"}
GET  /api/v1/admin/scrape-runs
GET  /api/v1/admin/scrape-runs/{run_id}
GET  /api/v1/admin/source-documents?run_id=...
GET  /api/v1/admin/promotions/{id}/overrides
POST /api/v1/admin/promotions/{id}/overrides
DELETE /api/v1/admin/overrides/{override_id}
```

El POST de corrida retorna `202` con una fila durable en PostgreSQL. Para procesarla debe estar activo el worker; alternativamente ejecutar `python -m app.scraping.cli worker --once` dentro de `api`. Una segunda solicitud pendiente del mismo banco retorna `409`. Los antiguos GET `/scraping/...` retornan `410`.

Una corrección utiliza `offer_key`, `patch` compatible con `OfferData` y `reason`. Se aplica sobre la extracción original, persiste ante futuros scrapes y regenera el calendario. Su listado informa si cambió la extracción que se revisó. DELETE desactiva la corrección, preservando el historial.

Las correcciones y la regeneración de calendario comparten el lock del banco con la ingesta; una corrección concurrente con una actualización responde `409` para reintentar al terminar. Al reiniciar, el worker recupera corridas interrumpidas y las vuelve a encolar sin duplicarlas.

Los bytes descargados se conservan por hash SHA-256. `source_documents` enlaza corrida, URL, MIME, fecha y versión; la evidencia de las ofertas incluye páginas y documentos cuando es posible. Para repetir una corrida con sus documentos guardados, sin red:

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli replay --document-id 1
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli refresh-calendar
```

Reemplazar `1` por un ID real de `/admin/source-documents`. Replay crea una nueva corrida y vuelve a guardar ofertas; si falta un documento, falla de forma explícita y no sale a Internet. Conservar juntos backup de PostgreSQL y volumen de snapshots.

Para leer un PDF escaneado o una imagen ya capturada mediante OCR local:

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec api python -m app.scraping.cli ocr-review --document-id 1 --pages 1
```

La imagen Docker incluye Tesseract y español. El JSON se guarda en `/app/data/review` con texto, página, confianza y coordenadas, ligado al hash del snapshot. Es material para cotejar con el original antes de una corrección administrativa; no publica ofertas ni modifica la base.

## IA opcional

El scraping de los cinco bancos utiliza parsers deterministas. Gemini está desactivado por defecto. Para pedir una propuesta sobre un documento guardado y una oferta existente, configurar `ENABLE_AI_ENRICHMENT=true`, `GEMINI_API_KEY` y, si corresponde, `AI_MODEL`, y ejecutar:

```text
python -m app.scraping.cli propose --document-id 1 --offer-id 1
```

Solicita una generación por propuesta, con reintentos acotados, límite de contexto y salida; conserva páginas completas y valida schema, identidad y citas contra el documento. Cachea por hash de documento, oferta, prompt, schema y modelo. **Devuelve una propuesta pendiente y no modifica ofertas publicadas.** Revisar las asociaciones antes de aplicar una corrección administrativa. Esta función no se invocó contra Gemini durante la implementación. Los módulos antiguos de enriquecimiento no forman parte del runner nuevo.

## Configuración

| Variable | Valor por defecto / uso |
| --- | --- |
| `SCRAPING_BANKS` | `ueno,sudameris,itau,atlas,gnb` |
| `SCRAPING_INTERVAL_SECONDS` | `86400`, intervalo por banco |
| `SCRAPING_TIMEOUT_SECONDS` | `1800`, presupuesto de una corrida |
| `SCRAPING_MAX_DOCUMENTS` | `1500` |
| `SCRAPING_MAX_DETAILS_PER_BANK` | `600`; truncar un catálogo genera `partial` |
| `SNAPSHOT_DIR` | `data/snapshots` dentro del volumen `/app/data` |
| `SCRAPING_RETIRE_MISSING` | `false`; si se activa, exige dos corridas completas sin presencia y vigencia conocida ya vencida |
| `ADMIN_API_KEY` | Privada; nunca enviar al frontend |
| `API_ACCESS_KEY` | Clave de lectura para despliegues directos; Next.js la envía como `X-API-Key` desde el servidor |
| `REQUIRE_API_ACCESS_KEY` | `false` en local; Render exige `true` y falla al iniciar si falta la clave |
| `ENABLE_AI_ENRICHMENT` | `false` |
| `AI_MODEL` | `gemini-2.5-flash` |
| `CORS_ORIGINS` | `http://localhost:3000` |

HTTP secuencial por banco: timeout 30 s, reintentos para 429/5xx, redirects limitados, dominios bancarios permitidos y descarga máxima 25 MB. Requests sigue siendo el cliente principal; el fallback HTTPX está habilitado solamente para un 403 en el host de beneficios GNB y comparte cache, snapshots y replay. No ejecuta JavaScript ni invoca IA. Una caída de cobertura respecto de la última corrida completa genera `partial`. Las corridas fallidas/parciales no retiran ausencias.

## Pruebas

Las pruebas de integración requieren una **base de prueba separada**; nunca apuntarlas a la base real. Crearla una vez en el PostgreSQL local:

```powershell
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml exec db createdb -U postgres test_banks_discounts
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r api/requirements.lock -r api/requirements-test.lock
cd api
$env:DB_HOST='localhost'
$env:DB_NAME='test_banks_discounts'
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/test_banks_discounts'
../.venv/Scripts/python.exe -m pytest -q
```

Las fixtures bancarias son offline; no requieren Gemini ni Internet. Las pruebas PostgreSQL usan esquemas aislados y comprueban migración, disponibilidad, idempotencia, snapshots, cola y correcciones. La web documenta sus pruebas unitarias y de navegador en su propio README. Docker instala dependencias fijadas en `api/requirements.lock`.

Las pruebas de cada adaptador están en `api/tests/banks/test_<banco>.py`; los helpers no importan otros módulos de pruebas. Para comprobar solamente los formatos bancarios desde `api/`:

```powershell
../.venv/Scripts/python.exe -m pytest -q tests/banks tests/test_schedule_extraction.py tests/test_document_acquisition.py
```

## Contrato y CI

`api/contracts/openapi.json` se exporta desde la aplicación FastAPI y se versiona. El frontend genera sus tipos y validadores desde ese esquema; las respuestas incompatibles se rechazan antes de renderizar condiciones.

```powershell
# Desde api/, después de cambiar una respuesta pública:
../.venv/Scripts/python.exe -m scripts.export_openapi
# Desde ../banks-discounts-web/:
npm.cmd run contracts:update -- ../banks-discounts-py/api/contracts/openapi.json
```

Para comprobar ambos repositorios con la API real, instalar las dependencias de la web y ejecutar desde `api/` con `TEST_DATABASE_URL` configurada:

```powershell
../.venv/Scripts/python.exe -m scripts.check_frontend_contract
```

La prueba inicia FastAPI en un puerto local temporal y aplica las migraciones a un esquema aislado de PostgreSQL de pruebas. Usa datos controlados, comprueba el contrato y elimina el esquema al cerrar. No descarga bancos ni utiliza la base de desarrollo.

Los workflows de ambos repositorios se ejecutan con push y pull request. El backend comprueba tests, migraciones, contrato y build Docker. El frontend comprueba tipos, validadores, lint, unitarias, build, navegador e integración con el backend. La comprobación inversa desde backend se habilita configurando `FRONTEND_REPOSITORY` cuando exista el remoto de la web. Ver [configuración de CI y contratos](api/docs/ci-and-contracts.md), incluyendo cómo seleccionar las ramas de dos PRs coordinados. CI valida cambios; el despliegue sigue siendo un paso separado.

## Estructura

```text
api/app/promotions/              Contrato, calendario, legacy y correcciones
api/app/scraping/sources/        Adquisición particular por banco
api/app/scraping/parsers/        Interpretación pura de HTML/JSON/PDF
api/app/scraping/runner.py       Corridas, presupuestos, locks y snapshots
api/app/scraping/persistence.py  Identidad estable y publicación transaccional
api/app/scraping/cli.py          Run, worker, replay y propuestas
api/app/scraping/registry.py     Bancos y versión de adaptadores compartidos
api/app/scraping/experimental/   Código anterior aislado y ejecución opt-in
api/app/database/models/        Modelos PostgreSQL
api/alembic/versions/            Historial y migración aditiva
api/tests/                      Pruebas offline e integración
api/tests/banks/                Casos independientes por banco
api/contracts/                  OpenAPI generado y versionado
api/scripts/                    Exportador y prueba del contrato real
.github/workflows/ci.yml         Verificaciones automáticas del backend
```

Documentación: [esquema actual](api/docs/database/schema-overview.md), [ADR de calendario e ingesta](api/docs/adr/005-offers-calendar-and-ingestion.md), [arquitectura de scrapers](api/app/scraping/scraping_architecture.md), [índice de documentos](api/docs/README.md).

Para desplegar gratis una primera versión personal, seguir la [guía Oracle Cloud + Vercel](api/docs/deployment/oracle-free.md). Producción utiliza `deploy.env` (ver [plantilla](deploy.env.example)), un proxy HTTPS con clave privada y backups conjuntos de PostgreSQL/documentos. PostgreSQL y la API no publican puertos directos. Esta preparación todavía no publica la aplicación.

Si Oracle no tiene capacidad, la [guía Render Free + Neon + Vercel](api/docs/deployment/render-neon-free.md) prepara otra prueba personal: API privada en Render, PostgreSQL en Neon y scrapers/documentos locales. [render.yaml](render.yaml) fija el plan gratuito; [docker-compose.neon.yml](docker-compose.neon.yml) conecta la CLI a Neon con [neon.env.example](neon.env.example). Las corridas terminan al completar los bancos para permitir que Neon se suspenda cuando no hay consultas. `/healthz` verifica que la API está viva sin abrir conexiones a la base; `/api/v1/health`, catálogos, promociones y documentación requieren la clave cuando se configura. La administración conserva una credencial independiente y está deshabilitada en esta prueba de Render.
