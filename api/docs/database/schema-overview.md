# Esquema de base de datos: visión general

## Objetivo

La base de datos del proyecto tiene como objetivo almacenar promociones bancarias de forma estructurada, permitiendo su consulta, trazabilidad y evolución a medida que se incorporen nuevas fuentes y reglas de negocio.

El diseño inicial busca un equilibrio entre:

- claridad
- extensibilidad
- simplicidad de implementación

## Bloques principales del modelo

El esquema inicial se organiza alrededor de los siguientes bloques conceptuales:

### 1. Entidades financieras

Representan los actores emisores de promociones.

Tablas principales:

- `banks`

### 2. Productos de tarjeta

Describen las distintas combinaciones de marca y producto asociadas a las promociones.

Tablas principales:

- `card_brands`
- `card_products`

### 3. Clasificación comercial

Permiten agrupar y categorizar promociones según tipo de comercio o rubro.

Tablas principales:

- `categories`
- `merchant_groups`

### 4. Ubicación de comercios

Representan los lugares o sucursales donde una promoción puede aplicar.

Tablas principales:

- `merchant_locations`

### 5. Campañas

Agrupan promociones bajo una lógica común de vigencia o publicación.

Tablas principales:

- `campaigns`

### 6. Promociones

Representan la oferta concreta visible para el usuario final.

Tablas principales:

- `promotions`

## Relaciones generales

A alto nivel, el esquema contempla relaciones como las siguientes:

- un banco puede tener múltiples campañas
- una campaña puede tener múltiples promociones
- una promoción puede estar asociada a una categoría
- una promoción puede estar asociada a un grupo de comercios
- una promoción puede aplicar a una ubicación comercial
- una promoción puede depender de una marca o producto de tarjeta

El detalle exacto de claves foráneas y cardinalidades deberá quedar reflejado en los modelos y migraciones, pero esta visión general ayuda a entender la intención del diseño.

## Convenciones adoptadas

### Nombres de tablas

Se utiliza plural para nombres de tablas:

- `banks`
- `card_brands`
- `merchant_locations`

### Nombres de columnas y atributos

Se utiliza `snake_case`.

### Organización de modelos

Se adopta un archivo por modelo para mantener claridad y escalabilidad.

### Enums

Los enums se ubican en:

- `api/app/database/enums/`

Esto responde a que, en la etapa actual, están principalmente ligados a persistencia y modelos SQLAlchemy.

## Principios de diseño

- Empezar por un núcleo estable antes de modelar casos complejos.
- Evitar sobreingeniería en la primera migración.
- Permitir crecimiento por etapas mediante nuevas migraciones.
- Documentar explícitamente qué entra y qué queda fuera.

## Documentos relacionados

- `docs/database/first-migration-scope.md`
- `docs/database/future-tables.md`
- `docs/adr/003-initial-promotion-data-model.md`
- `docs/adr/004-enums-in-database-layer.md`