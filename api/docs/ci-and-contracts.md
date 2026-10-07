# CI y contrato entre backend y frontend

Implementado el 5 de octubre de 2026. Los workflows validan cambios; no publican
imágenes, no despliegan y no hacen scraping bancario durante las pruebas.

## Contrato versionado

El backend mantiene `api/contracts/openapi.json`, generado desde `app.main.app`
sin conectar a PostgreSQL. Los modelos de respuesta describen como requeridos
los defaults y valores nullable que FastAPI incluye al serializar.

Desde `api/`:

```powershell
../.venv/Scripts/python.exe -m scripts.export_openapi
../.venv/Scripts/python.exe -m scripts.export_openapi --check
```

El modo `--check` falla si el archivo falta o difiere de la aplicación. No escribe
archivos. Cambiar el modelo sin actualizar el contrato hace fallar CI.

Desde el frontend:

```powershell
npm.cmd run contracts:update -- ../banks-discounts-py/api/contracts/openapi.json
npm.cmd run contracts:check -- ../banks-discounts-py/api/contracts/openapi.json
```

También se acepta una URL de OpenAPI en `contracts:update`. El flujo habitual
usa el export local para no depender de un servidor levantado.

La web versiona su copia del esquema, tipos TypeScript y validadores generados
para bancos, categorías, listado y detalle. `contracts:generate` regenera desde
la copia local; `contracts:check` comprueba esos artefactos y, si se pasa una
fuente, compara además ambos esquemas. Nunca editar los artefactos a mano.
Las reglas `.gitattributes` conservan LF para reproducibilidad en Windows.

## Prueba real entre repositorios

Preparar una base PostgreSQL cuyo nombre contenga `test`, instalar los locks
Python y ejecutar `npm ci` en la web. Desde `api/`:

```powershell
$env:DB_HOST='localhost'
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/test_banks_discounts'
../.venv/Scripts/python.exe -m scripts.check_frontend_contract
```

El script encuentra el frontend hermano. Para otro checkout usar
`--frontend <ruta>`. Inicia la misma aplicación FastAPI con sus rutas,
middleware, modelos y serializadores, sobrescribiendo la sesión de base de datos
por un esquema nuevo de la base de pruebas. Aplica Alembic y carga una promoción
controlada con dos variantes y dos locales, además de una promoción pendiente.
La fecha fija evita que la prueba dependa del día de ejecución.

La web consulta la API por HTTP real y comprueba OpenAPI, catálogos,
listado/detalle, ciudades, evidencia, pendientes y filtros. No hay servidor fixture
en esta comprobación. El proceso se cierra y el esquema se elimina al terminar,
incluso cuando la prueba falla. Ningún dato controlado se guarda en la base de
desarrollo y no se ejecutan requests a bancos ni proveedores de IA.

La suite Playwright conserva su API fixture para probar errores, navegación y
pantallas; complementa esta comprobación con PostgreSQL y FastAPI.

## Workflows

| Repositorio | Comprobaciones |
| --- | --- |
| Backend | Locks Python, OpenAPI actualizado, suite con PostgreSQL 16, migración desde cero, drift del esquema, imagen Docker y Compose. |
| Frontend | Locks npm, generación reproducible, tipos, lint, unitarias, build, Playwright y contrato con un checkout real del backend. |
| Backend, cuando se configura el remoto frontend | La revisión backend del PR se contrasta también con el frontend elegido y sus validadores. |

Ambos usan `push`, `pull_request` y ejecución manual. Los runners de CI son
Linux; las verificaciones locales también funcionan en Windows. Referencias de
configuración: [servicios PostgreSQL](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)
y [checkout de otro repositorio](https://github.com/actions/checkout).

## Configuración de los dos repositorios

El frontend todavía necesita un remoto GitHub. Primero subir el backend con los
scripts nuevos a la referencia que la CI del frontend utilizará. El workflow de
la web comprueba ese checkout siempre; no omite integración cuando falta código.

Variables de repositorio en GitHub:

| Dónde | Variable | Valor |
| --- | --- | --- |
| Frontend | `BACKEND_REPOSITORY` | Opcional; default `mbutlerov/banks-discounts-py`. |
| Frontend | `BACKEND_REF` | Opcional; rama, tag o SHA. Vacío usa la rama por defecto del backend. |
| Backend | `FRONTEND_REPOSITORY` | `propietario/repositorio`; habilita la comprobación inversa cuando la web esté en GitHub. |
| Backend | `FRONTEND_REF` | Opcional; rama, tag o SHA del frontend. |

Para PRs que cambian ambos contratos, seleccionar en estas variables las ramas
correspondientes mientras se integran. Después volver a la referencia estable.
Un SHA fija de forma explícita qué versión del otro repositorio se verifica.

Si el otro repositorio es privado, configurar `BACKEND_READ_TOKEN` en frontend
y/o `FRONTEND_READ_TOKEN` en backend, con permiso de lectura sobre el repositorio
correspondiente. Los tokens se guardan como secrets de GitHub, nunca en archivos.
En repositorios públicos no hace falta un token adicional.

Hasta configurar `FRONTEND_REPOSITORY`, el backend verifica su contrato y pruebas
propias, pero no detecta por sí solo incompatibilidades con un frontend remoto.
La prueba local entre ambos y el job de integración frontend sí las detectan.
Actualizar el esquema y los artefactos de los dos repositorios en cambios
coordinados antes de desplegar una respuesta incompatible.

## Validación de este pase

Backend: 345 pruebas con PostgreSQL de pruebas (incluidas las nuevas regresiones
GNB y HTTPX del 6 de octubre), export OpenAPI comprobado,
imagen Docker construida y Compose validado. Actionlint verificó ambos workflows
sin errores. Frontend: 11 unitarias, 5 pruebas
contra FastAPI real, 13 pruebas de navegador, generación reproducible, tipos,
lint y build. Los workflows quedan preparados; la ejecución en GitHub ocurrirá
cuando se suban los cambios y se configuren las referencias necesarias.
