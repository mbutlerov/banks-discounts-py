# Despliegue personal gratuito: Render + Neon + Vercel

Revisado el **7 de octubre de 2026**. Esta variante permite consultar la web
desde Internet aunque la computadora esté apagada. Las actualizaciones bancarias
y los documentos se procesan en la computadora mediante Docker.

| Componente | Ubicación | Función |
| --- | --- | --- |
| Web Next.js | Vercel Hobby | Interfaz y consultas a la API desde el servidor |
| API FastAPI | Render Web Service Free | Consulta datos; exige `X-API-Key` |
| PostgreSQL | Neon Free | Conserva promociones, locales, reglas e historial |
| Scraping y OCR | Docker Desktop local | Actualiza Neon y guarda PDFs en un volumen local |

Elegir los planes gratuitos en los tres proveedores. Esta guía no crea cuentas,
publica código ni configura credenciales automáticamente.

## 1. Crear PostgreSQL en Neon

En [Neon Console](https://console.neon.tech), crear un proyecto:

| Campo | Valor |
| --- | --- |
| Nombre | `banks-discounts` |
| Plan | Free |
| Proveedor y región | AWS, US East 2 / Ohio |
| PostgreSQL | 16, si aparece en el selector; es la versión usada en nuestras pruebas |

Usar también **Ohio** al crear la API en Render, para mantener próximos ambos
servicios. [Regiones de Render](https://render.com/docs/regions).

Abrir **Connect**, seleccionar la rama principal del proyecto, la base y el
usuario. Desactivar **Connection pooling** y guardar la conexión **directa**:
su hostname no debe incluir `-pooler`. Usar esta conexión tanto en Render como
en los comandos locales. Nuestro scraper utiliza locks de sesión PostgreSQL;
el pooler de Neon funciona por transacción y no conserva esos locks.
[Conexiones de Neon](https://neon.com/docs/connect/connect-from-any-app),
[límites del pooler](https://neon.com/docs/connect/connection-pooling).

Separar los datos de conexión en `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` y
`DB_PASSWORD`. `DB_HOST` contiene solamente el hostname; no pegar allí una URL
`postgresql://...`. Conservar `DB_SSLMODE=require` para cifrar la conexión.
Si la contraseña aparece codificada dentro de una URL, usar la contraseña real
del usuario, sin codificación de URL, en `DB_PASSWORD`.

Guardar los secretos en los archivos locales ignorados y en los paneles de
los proveedores. No pegarlos en el chat ni en capturas de pantalla.

## 2. Conectar el scraper local a Neon

Desde `banks-discounts-py`, con Docker Desktop activo, crear una sola vez el
archivo local:

```powershell
Copy-Item -LiteralPath neon.env.example -Destination neon.env
```

Editar `neon.env` con la conexión directa obtenida en Neon. No reemplazar un
archivo existente que ya contiene credenciales. `neon.env` está ignorado por
Git; `neon.env.example` conserva solamente valores ilustrativos.

```dotenv
ENV=neon
DB_HOST=ep-TU-ENDPOINT.us-east-2.aws.neon.tech
DB_PORT=5432
DB_NAME=neondb
DB_USER=TU_USUARIO
DB_PASSWORD='TU_CONTRASENA_REAL'
DB_SSLMODE=require
ENABLE_AI_ENRICHMENT=false
```

Las comillas simples en el archivo Compose de entorno conservan caracteres
como `$` literalmente. No pegar la clave de lectura de la API ni la clave de
administración: la CLI escribe directamente a PostgreSQL.

Construir la imagen y preparar la base nueva:

```powershell
docker compose --env-file neon.env -p banks-neon -f docker-compose.neon.yml build scraper

docker compose --env-file neon.env -p banks-neon -f docker-compose.neon.yml run --rm scraper python -m alembic upgrade head

docker compose --env-file neon.env -p banks-neon -f docker-compose.neon.yml run --rm scraper python -m app.database.seed
```

Estos comandos se conectan a Neon; no crean un PostgreSQL local ni modifican
`banks-dev`. El seed es idempotente. Para futuras migraciones de una base ya
poblada, guardar un backup primero y ejecutar la migración sin un scraper activo.

Probar primero un banco y después completar el catálogo:

```powershell
docker compose --env-file neon.env -p banks-neon -f docker-compose.neon.yml run --rm scraper python -m app.scraping.cli run --bank itau

docker compose --env-file neon.env -p banks-neon -f docker-compose.neon.yml run --rm scraper python -m app.scraping.cli run --bank all
```

También acepta `ueno`, `sudameris`, `atlas` y `gnb`. Una corrida `partial`
conserva los descuentos válidos y los casos pendientes; una `failed` devuelve
exit code 1. En PowerShell se puede revisar `$LASTEXITCODE` al terminar.

El volumen de este stack conserva `/app/data`, incluyendo snapshots y revisiones
OCR, aunque el contenedor temporal se elimine con `--rm`. Mantener siempre el
proyecto `banks-neon` para reutilizarlo. Evitar `down -v` y la eliminación del
volumen desde Docker Desktop, porque borran los documentos.

## 3. Publicar los repositorios en GitHub

Render necesita una rama que contenga `api/docker/start-render.sh`, los cambios
de autenticación, el Dockerfile actualizado y las migraciones. Vercel necesita
los cambios de frontend que envían la clave desde el servidor y esperan el
arranque de Render.

Revisar los commits locales y publicar la rama elegida en GitHub.
El backend ya tiene un remoto; el frontend necesita su propio repositorio remoto
antes de importarlo desde GitHub. Elegir en cada proveedor la rama que realmente
contiene estos cambios. Crear un servicio contra una rama anterior no publica
los archivos que están solamente en esta computadora.

No subir `neon.env`, `deploy.env`, `api/environments/private.env`, `.env.local`,
backups ni snapshots. No configurar credenciales dentro de `render.yaml`.

## 4. Crear la API gratuita en Render

En [Render Dashboard](https://dashboard.render.com), elegir **New → Web Service**,
conectar GitHub y seleccionar `banks-discounts-py`.

| Campo | Valor |
| --- | --- |
| Name | `banks-discounts-api`, o un nombre disponible |
| Region | Ohio |
| Branch | La rama publicada con los cambios |
| Language / Runtime | Docker |
| Root Directory | Vacío: raíz del repositorio |
| Dockerfile Path | `./api/Dockerfile` |
| Docker Build Context Directory | `./api` |
| Docker Command | `sh docker/start-render.sh` |
| Instance Type | Free |
| Health Check Path | `/healthz` |

Docker usa el contexto `api`; el comando se ejecuta dentro de `/app` en la imagen.
Render proporciona HTTPS en el subdominio `onrender.com` y la variable `PORT`;
el script de arranque enlaza Uvicorn a `0.0.0.0:$PORT`.
[Docker en Render](https://render.com/docs/docker),
[servicios web](https://render.com/docs/web-services).

También existe [render.yaml](../../../render.yaml) para crear el servicio con
**New → Blueprint**. Usar una sola de las dos vías para evitar crear servicios
duplicados. Las claves marcadas `sync: false` se completan en el panel.

Antes del primer despliegue, configurar estas variables:

| Variable | Valor |
| --- | --- |
| `ENV` | `render` |
| `REQUIRE_API_ACCESS_KEY` | `true` |
| `API_ACCESS_KEY` | Una clave aleatoria larga, privada y exclusiva para esta API |
| `DB_HOST` | Hostname directo de Neon, sin `-pooler` |
| `DB_PORT` | `5432` |
| `DB_NAME` | Base elegida en Neon |
| `DB_USER` | Usuario elegido en Neon |
| `DB_PASSWORD` | Contraseña real del usuario |
| `DB_SSLMODE` | `require` |
| `CORS_ORIGINS` | Vacío; las consultas provienen del servidor de Vercel |
| `ENABLE_AI_ENRICHMENT` | `false` |

Mantener `ADMIN_API_KEY` sin configurar: la administración HTTP queda
deshabilitada. En esta variante, Render ejecuta solamente la API; las
migraciones, el seed y los scrapers se ejecutan mediante la CLI local. No agregar
un background worker, una base Render Postgres ni un disco de pago.

`/healthz` es público y devuelve una respuesta mínima sobre el proceso; no
consulta PostgreSQL. Las consultas de datos, `/api/v1/health` y la documentación
de la API exigen `X-API-Key`. La clave de lectura y `ADMIN_API_KEY` tienen usos
distintos; tener la primera no habilita administración.
[Health checks de Render](https://render.com/docs/health-checks).

## 5. Publicar la web en Vercel

Importar el repositorio `banks-discounts-web` en
[Vercel](https://vercel.com/new), con plan **Hobby**:

| Campo | Valor |
| --- | --- |
| Framework | Next.js |
| Root Directory | Raíz del repositorio frontend |
| Node.js | 22.x |
| Build Command | `npm run build` |
| Output Directory | Predeterminado de Next.js |

Configurar las variables para **Production** y, si se van a utilizar,
**Preview**:

```dotenv
API_BASE_URL=https://TU-SERVICIO.onrender.com/api/v1
API_ACCESS_KEY=LA_MISMA_CLAVE_PRIVADA_CONFIGURADA_EN_RENDER
API_TIMEOUT_MS=120000
```

`API_ACCESS_KEY` se usa desde código `server-only`. Conservar su nombre privado;
no crear una variable `NEXT_PUBLIC_API_ACCESS_KEY`, ni incluirla en enlaces o
componentes de navegador. El timeout de dos minutos permite esperar la primera
respuesta cuando Render está dormido. Si se cambian variables, hacer un nuevo
deploy para aplicarlas.
[Variables en Vercel](https://vercel.com/docs/environment-variables/managing-environment-variables),
[Node.js en Vercel](https://vercel.com/docs/functions/runtimes/node-js/node-js-versions).

## 6. Limitar el acceso a tu cuenta

En el proyecto Vercel, abrir **Security → Deployment Protection → Vercel
Authentication** y seleccionar **All Deployments**, incluyendo producción.
Desde el 9 de septiembre de 2026 esta protección está disponible sin costo
adicional en todos los planes. La web requiere iniciar sesión con una cuenta
Vercel que tenga acceso al proyecto.
[Anuncio oficial](https://vercel.com/changelog/protect-production-deployments-for-free-on-every-plan).

La URL de Render sigue siendo pública, pero sus datos requieren la clave que
solo guarda el servidor de Vercel. Evitar excepciones de protección y enlaces de
acceso compartido si la web debe ser exclusivamente personal.

## 7. Verificar el despliegue

1. Abrir `https://TU-SERVICIO.onrender.com/healthz`: debe responder con el estado
   mínimo del proceso. La primera visita puede esperar mientras Render despierta.
2. Con la API ya despierta, consultar `/api/v1/catalogs/banks` sin `X-API-Key`:
   debe devolver `401`. Esta es una comprobación de acceso; no configurar ese
   endpoint como health check de Render.
3. Abrir la URL de producción de Vercel desde una sesión sin autenticar:
   debe solicitar acceso de Vercel. Iniciar sesión con la cuenta propietaria.
4. Consultar los bancos, filtrar por Itaú y abrir un descuento vigente. Deben
   aparecer los datos guardados en Neon sin descargar promociones durante la
   navegación. Los filtros pueden devolver cero cuando no hay ofertas para la fecha.
5. Revisar las últimas fechas de actualización y los logs de Render si la web
   muestra un error de conexión. Confirmar host directo, SSL y claves iguales.

Apagar la computadora no impide consultar los datos guardados. Sí detiene las
nuevas actualizaciones; la fecha de comprobación permite ver cuándo se hizo la
última corrida.

## 8. Actualizar y conservar los documentos

Para actualizar, repetir `run --bank all` o el banco deseado desde PowerShell.
Se puede crear una tarea diaria en el Programador de tareas de Windows que
ejecute ese comando desde la carpeta del repositorio y termine. Requiere que
la computadora y Docker Desktop estén activos a esa hora.

No dejar el worker `--schedule` encendido permanentemente en esta variante:
consulta Neon cada diez segundos y evita que el compute descanse. La operación
por CLI termina al finalizar cada corrida y facilita mantenerse dentro de la
cuota gratuita.

Neon conserva metadatos y rutas relativas; los bytes de PDFs permanecen en el
volumen local. Por eso los comandos `replay` y `ocr-review` deben ejecutarse en
este mismo stack:

```powershell
docker compose --env-file neon.env -p banks-neon -f docker-compose.neon.yml run --rm scraper python -m app.scraping.cli replay --document-id 123

docker compose --env-file neon.env -p banks-neon -f docker-compose.neon.yml run --rm scraper python -m app.scraping.cli ocr-review --document-id 123 --pages 1 2
```

Reemplazar `123` por un documento real. Render no conserva una copia de esos
archivos; los enlaces oficiales de las promociones siguen apuntando al banco.

Guardar backups de PostgreSQL mediante `pg_dump` con la conexión directa y una
copia del volumen local, conservando ambas partes juntas y fuera de la
computadora. La [guía de backup de Neon](https://neon.com/docs/postgres/backup-restore/backups)
describe las opciones de PostgreSQL y restauración. El script
`backup-production.sh` corresponde al stack de servidor Oracle, que tiene su
propio contenedor `db`; no aplicarlo a esta variante.

## 9. Límites del costo cero

Render Free comparte **750 horas mensuales por workspace**, duerme tras **15
minutos** sin tráfico y puede tardar aproximadamente **un minuto** en arrancar.
No ofrece discos persistentes, shell ni trabajos one-off. Su PostgreSQL gratuito
vence a los **30 días**, por eso usamos Neon. Revisar también las cuotas de
tráfico y builds: superar límites puede suspender servicios o generar cargos
si hay un método de pago configurado.
[Condiciones de Render Free](https://render.com/docs/free).

El anuncio vigente de Neon indica **1 GB de PostgreSQL** y **100 CU-h mensuales
por proyecto**, con una ventana de restauración de **6 horas**. No es una cuota
ilimitada: vigilar almacenamiento y consumo en el panel. También anuncia 5 GB
de almacenamiento de objetos, pero esta implementación todavía guarda los
PDFs en Docker local; no los sube automáticamente a Neon.
[Límites anunciados el 2 de octubre de 2026](https://neon.com/blog/neon-free-plan-1-gb-per-project).

Esta configuración permite una prueba personal con infraestructura gratuita y
actualización local. Para actualizar bancos sin depender de la computadora,
hará falta conseguir una máquina persistente o incorporar otro mecanismo de
ejecución y almacenamiento.
