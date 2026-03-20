
---

## `docs/adr/001-compose-by-environment.md`

# ADR 001: Compose por entorno

## Estado

Aceptado

## Contexto

El proyecto necesita ejecutarse en distintos contextos, principalmente desarrollo y producción. Aunque ambos comparten una base común, no necesariamente requieren la misma configuración de servicios, volúmenes, variables de entorno o comportamiento operativo.

Si toda la configuración se concentrara en un único archivo `docker-compose.yml`, con el tiempo ese archivo se volvería más difícil de mantener, más propenso a errores y menos claro para quienes se incorporen al proyecto.

## Decisión

Se decidió utilizar una estrategia de múltiples archivos Compose:

- `docker-compose.yml` como base común
- `docker-compose.dev.yml` para personalización de desarrollo
- `docker-compose.prod.yml` para personalización de producción

La idea es que el archivo base contenga únicamente la configuración compartida, mientras que cada archivo adicional ajuste lo necesario según el entorno.

## Consecuencias

### Positivas

- La configuración común queda centralizada.
- Cada entorno puede extender la base sin duplicar todo.
- La intención de cada archivo es clara.
- Se reduce el acoplamiento entre necesidades de desarrollo y producción.
- Escalar la configuración a futuro resulta más simple.

### Negativas

- Levantar el entorno requiere usar más de un archivo.
- Es necesario conocer el orden correcto de composición.
- Puede haber confusión inicial si no está bien documentado.

## Justificación

Esta estrategia ofrece un mejor balance entre reutilización y claridad. Permite crecer sin convertir la configuración de contenedores en un único archivo monolítico difícil de mantener.

## Alternativas consideradas

### 1. Un solo archivo Compose para todos los entornos

Se descartó porque mezcla necesidades distintas en un mismo lugar y tiende a crecer desordenadamente.

### 2. Un archivo completamente separado por entorno, sin base común

Se descartó porque duplicaría configuración y aumentaría el costo de mantenimiento.

## Notas

La documentación de uso operativo de estos archivos se encuentra en:

- `docs/docker/environment-setup.md`