# ADR 003: Alcance inicial del modelo de datos de promociones

## Estado

Aceptado

## Contexto

El proyecto busca construir una base de datos capaz de almacenar promociones bancarias de forma estructurada, consultable y extensible.

Sin embargo, intentar modelar desde el inicio todos los posibles detalles del dominio puede hacer que la primera etapa se vuelva demasiado grande, difícil de validar y más costosa de corregir.

Por ese motivo, se necesitaba definir un conjunto inicial de tablas que permitiera:

- establecer la base del dominio
- crear relaciones principales
- habilitar futuras migraciones
- evitar sobreingeniería en una etapa temprana

## Decisión

Se decidió limitar la primera etapa del modelado a las siguientes tablas:

- `banks`
- `card_brands`
- `card_products`
- `categories`
- `merchant_groups`
- `merchant_locations`
- `campaigns`
- `promotions`

Estas tablas representan el núcleo del dominio y permiten describir promociones bancarias de manera suficientemente útil para una primera versión.

## Consecuencias

### Positivas

- Se reduce el alcance inicial.
- La primera migración se vuelve más manejable.
- Se prioriza validar relaciones principales antes de modelar casos más específicos.
- Permite iterar más rápido sobre un núcleo estable.

### Negativas

- Algunos detalles del dominio quedarán fuera de la primera versión.
- Habrá que extender el esquema en migraciones futuras.
- Algunas promociones complejas todavía no podrán representarse con total fidelidad.

## Justificación

La prioridad en esta etapa es construir una base sólida y entendible, no capturar todos los casos del dominio desde el primer día.

Este conjunto de tablas permite empezar con una estructura razonable para:

- bancos
- tipos de tarjeta
- campañas
- comercios
- ubicaciones
- promociones concretas

## Alternativas consideradas

### 1. Modelar todo el dominio desde el inicio

Se descartó por alto costo de diseño, mayor incertidumbre y dificultad para validar rápido.

### 2. Empezar con un modelo excesivamente mínimo

También se descartó porque dejaría fuera relaciones importantes desde el principio y obligaría a rehacer parte del diseño demasiado pronto.

## Notas

El detalle de esta etapa se documenta en:

- `docs/database/schema-overview.md`
- `docs/database/first-migration-scope.md`
- `docs/database/future-tables.md`