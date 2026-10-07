# Documentación del proyecto

La [CI y el contrato entre repositorios](ci-and-contracts.md) describen las verificaciones automáticas, la generación de tipos/validadores y la prueba del frontend contra FastAPI y PostgreSQL reales.

Para retomar el proyecto, empezar por el [README operativo](../../README.md),
el [esquema vigente](database/schema-overview.md), la
[arquitectura de scraping](../app/scraping/scraping_architecture.md) y el
[ADR de ofertas, calendario e ingesta](adr/005-offers-calendar-and-ingestion.md).
Los documentos de la primera etapa se conservan como antecedentes; el esquema
vigente y los ADR 005/006 describen la implementación actual.

Este directorio contiene la documentación técnica del proyecto. Su objetivo es dejar registradas las decisiones importantes, el diseño inicial de la base de datos, la configuración de entornos y el flujo general de ingestión de datos.
La normalización comercial y sus comandos de revisión están documentados en [ADR 006](adr/006-merchants-and-location-membership.md) y [operación de comercios y locales](database/merchant-management.md).

Siglas `adr`=Architecture Decision Records.

La intención es que cualquier persona que tome el proyecto en el futuro pueda entender:

- qué decisiones ya fueron tomadas
- por qué se tomaron
- qué alcance tiene la etapa actual
- cómo continuar sin depender del historial del chat, commits o conocimiento implícito

## Estructura

```text
docs/
├── README.md
├── adr/
│   ├── 001-compose-by-environment.md
│   ├── 002-alembic-inside-container.md
│   ├── 003-initial-promotion-data-model.md
│   ├── 004-enums-in-database-layer.md
│   ├── 005-offers-calendar-and-ingestion.md
│   └── 006-merchants-and-location-membership.md
├── database/
│   ├── schema-overview.md
│   ├── first-migration-scope.md
│   ├── future-tables.md
│   └── merchant-management.md
├── docker/
│   └── environment-setup.md
├── deployment/
│   ├── oracle-free.md
│   └── render-neon-free.md
└── scraping/
    └── ingestion-flow.md
```

## Cómo usar esta documentación
#### 1. Si se quiere entender cómo está organizada la documentación
Leer este archivo.

#### 2. Si se quiere entender por qué se tomó una decisión técnica
Ir a `docs/adr/`.

Los ADRs (Architecture Decision Records) registran decisiones importantes de arquitectura y diseño. Cada uno explica el contexto, la decisión tomada y sus consecuencias.

#### 3. Si se quiere entender el diseño de base de datos
Ir a `docs/database/`.

Esta sección documenta el alcance de la primera etapa del modelado, la estructura general del esquema y las tablas previstas para etapas futuras.

#### 4. Si se quiere entender cómo se levantan los entornos con Docker
Ir a `docs/docker/environment-setup.md`.

#### 5. Si se quiere entender el flujo previsto de scraping e ingestión
Ir a `docs/scraping/ingestion-flow.md`.

#### 6. Si se quiere desplegar una prueba personal gratuita
Leer [Oracle Cloud + Vercel](deployment/oracle-free.md): instancia, secretos,
HTTPS, migraciones, frontend privado y backups.
La alternativa [Render Free + Neon + Vercel](deployment/render-neon-free.md)
publica la API y la base mientras la adquisición de documentos continúa localmente.

## Principios de documentación
Esta carpeta sigue los siguientes criterios:
* El README.md principal explica cómo navegar la documentación.
* Los ADRs documentan decisiones, no tutoriales de uso.
* La sección database/ documenta diseño y alcance del esquema.
* La sección docker/ documenta la estrategia de entornos.
* La sección scraping/ documenta el flujo funcional de ingestión.

## Mantenimiento
Cada vez que se tome una decisión técnica relevante o se cambie el alcance de una parte importante del sistema, se debe actualizar esta carpeta.

Regla sugerida:
* si cambia una decisión de arquitectura: crear o actualizar un ADR
* si cambia el modelo de datos: actualizar `docs/database/`
* si cambia la estrategia de entornos: actualizar `docs/docker/`
* si cambia el flujo de scraping: actualizar `docs/scraping/`
