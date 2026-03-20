# ADR 002: Ejecutar Alembic dentro del contenedor de aplicación

## Estado

Aceptado

## Contexto

El proyecto utiliza Docker para levantar tanto la aplicación como la base de datos. En este esquema, los servicios se comunican dentro de la red interna de Docker usando sus nombres de servicio.

Por ejemplo, la aplicación resuelve la base de datos mediante un host como:

- `db`

Ese host existe correctamente dentro de la red de contenedores, pero no necesariamente desde la máquina anfitriona.

Si Alembic se ejecuta fuera del contenedor, se generan varios problemas potenciales:

- diferencias entre el entorno local y el entorno real de la app
- dificultad para resolver el host de base de datos
- dependencia de tener configuraciones duplicadas fuera de Docker
- mayor riesgo de inconsistencias entre desarrollo y ejecución real

## Decisión

Se decidió ejecutar Alembic dentro del contenedor `api`.

Esto implica que las migraciones deben correr desde el mismo entorno donde corre la aplicación, compartiendo:

- red
- variables de entorno
- dependencias instaladas
- configuración de conexión

## Consecuencias

### Positivas

- El entorno de migraciones coincide con el entorno de ejecución real.
- Se evita duplicar configuración en la máquina host.
- La resolución del host de base de datos funciona de forma consistente.
- El flujo es más portable entre máquinas y miembros del equipo.

### Negativas

- El desarrollador debe acostumbrarse a ejecutar comandos dentro del contenedor.
- Puede parecer menos directo al inicio que correr Alembic localmente.

## Justificación

Dado que la aplicación vive dentro de Docker y la conexión a base depende de la red interna de contenedores, ejecutar Alembic dentro del contenedor es la forma más consistente y menos propensa a errores.

## Alternativas consideradas

### 1. Ejecutar Alembic desde la máquina host

Se descartó porque introduce dependencia en configuraciones locales, posibles diferencias de entorno y problemas de conectividad con la base.

### 2. Ejecutar Alembic desde un contenedor separado exclusivo para migraciones

No se adoptó en esta etapa porque agregaría complejidad innecesaria. Por ahora, el contenedor `api` ya cuenta con todo lo necesario para ejecutar migraciones.

## Notas

La forma práctica de levantar y usar los entornos se documenta en:

- `docs/docker/environment-setup.md`