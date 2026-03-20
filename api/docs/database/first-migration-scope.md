# Alcance de la primera migración

## Objetivo

La primera migración tiene como objetivo establecer la base estructural del proyecto sin intentar modelar todos los detalles posibles del dominio desde el inicio.

Esta etapa busca construir un núcleo inicial que permita:

- empezar a desarrollar modelos y relaciones principales
- validar el diseño general
- preparar el terreno para migraciones futuras

## Tablas incluidas

La primera migración incluye las siguientes tablas:

- `banks`
- `card_brands`
- `card_products`
- `categories`
- `merchant_groups`
- `merchant_locations`
- `campaigns`
- `promotions`

## Qué se busca lograr con esta etapa

Con estas tablas se espera cubrir la base necesaria para representar:

- bancos emisores
- marcas y productos de tarjetas
- categorías de promociones
- agrupaciones de comercios
- ubicaciones comerciales
- campañas promocionales
- promociones concretas

## Qué queda fuera por ahora

Quedan fuera de esta primera migración estructuras más específicas o complementarias, por ejemplo:

- tablas puente adicionales
- restricciones avanzadas de medios de pago
- topes y exclusiones
- trazabilidad detallada de scraping
- fuentes crudas de ingestión
- historial o auditoría extendida

Estas partes se documentan por separado para futuras etapas.

## Motivo de esta delimitación

Se eligió avanzar por etapas por las siguientes razones:

- reduce complejidad inicial
- permite validar primero el núcleo del dominio
- evita modelar prematuramente casos todavía no confirmados
- facilita iteración y corrección temprana

## Criterio de evolución

Una vez consolidada esta primera etapa, el esquema podrá ampliarse con nuevas migraciones según necesidades reales del negocio o del scraping.

La regla general es:

- primero estabilizar entidades principales
- luego incorporar detalle, trazabilidad y especialización

## Documentos relacionados

- `docs/database/schema-overview.md`
- `docs/database/future-tables.md`
- `docs/adr/003-initial-promotion-data-model.md`