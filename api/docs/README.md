# Documentación del proyecto

Este directorio contiene la documentación técnica del proyecto. Su objetivo es dejar registradas las decisiones importantes, el diseño inicial de la base de datos, la configuración de entornos y el flujo general de ingestión de datos.
Siglas `adr`=Architecture Desicion Records.

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
│   └── 004-enums-in-database-layer.md
├── database/
│   ├── schema-overview.md
│   ├── first-migration-scope.md
│   └── future-tables.md
├── docker/
│   └── environment-setup.md
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