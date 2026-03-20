# Configuración de entornos con Docker

## Objetivo

Este documento explica cómo se organiza la configuración de contenedores del proyecto y por qué se adoptó una estrategia basada en múltiples archivos Compose.

## Archivos utilizados

La configuración se divide en:

- `docker-compose.yml`
- `docker-compose.dev.yml`
- `docker-compose.prod.yml`

## Rol de cada archivo

### `docker-compose.yml`

Contiene la configuración base compartida entre entornos.

Debe incluir únicamente lo común, por ejemplo:

- servicios principales
- redes
- configuraciones reutilizables
- comportamiento no específico de desarrollo o producción

### `docker-compose.dev.yml`

Contiene ajustes específicos para desarrollo, por ejemplo:

- bind mounts
- configuración de recarga
- variables orientadas al trabajo local
- puertos expuestos para uso de desarrollo

### `docker-compose.prod.yml`

Contiene ajustes específicos para producción, por ejemplo:

- comportamiento más cercano al despliegue real
- menor dependencia de bind mounts
- configuración orientada a estabilidad y despliegue

## Forma de uso

### Desarrollo

```bash
docker-compose \
  -p banks-dev \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up -d --build
  ```

  ### Producción
  ```bash
  docker-compose \
  -p banks-prod \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up -d --build
  ```

  ### Uso de project name
  Se decidió utilizar `-p` para definir un nombre de proyecto explícito, por ejemplo:

- `banks-dev`
- `banks-prod`

Esto permite:
- aislar recursos por entorno
- evitar colisiones de nombres
- mantener claridad en redes, volúmenes y serviciios generados por docker compose.

### Por que no se usa `container_name`
No se recomienda fijar `container_name` manualmente porque:
- reduce flexibilidad
- puede generar conflictos entre entornos
- rompe parte del mecanismo natural de nombres de Compose
- complica levantar múltiples stacks similares en una misma máquina

Se prefiere dejar que Compose administre los nombres en función del project name y del nombre del servicio.

### Variables de entorno
La configuración de entorno debe apoyarse en archivos `.env` o equivalentes según la estrategia adoptada por el proyecto.
La idea general es:
- centralizar configuración sensible o variable.
- evitar hardcodear valores dentro de los archivos Compose
- permitir diferencias claras entre desarrollo y producción.

### Relación con Alembic
Dado que la aplicación y la base de datos viven dentro de la red Docker, las migraciones deben ejecutarse dentro del contenedor de aplicación para compartir el mismo entorno y resolver correctamente dependencias como el host `db`.

### Documentos relacionados

- `docs/adr/001-compose-by-environment.md`
- `docs/adr/002-alembic-inside-container.md`