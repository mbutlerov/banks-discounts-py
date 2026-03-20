# Tablas previstas para etapas futuras

## Objetivo

Este documento registra tablas y estructuras que probablemente serán necesarias en futuras iteraciones del proyecto, pero que no forman parte de la primera migración.

El objetivo es dejar constancia de la dirección prevista del esquema sin forzar su implementación antes de tiempo.

## Posibles tablas futuras

### `promotion_locations`

Podría utilizarse si una promoción necesita asociarse a múltiples ubicaciones comerciales en lugar de depender de una única referencia directa.

### `payment_methods`

Podría representar medios de pago aceptados o requeridos por una promoción.

### `promotion_payment_methods`

Tabla puente para modelar relación muchos a muchos entre promociones y medios de pago.

### `promotion_benefits`

Podría separar la definición del beneficio en una estructura más flexible, por ejemplo:

- porcentaje de descuento
- monto fijo
- cuotas
- reintegro

### `promotion_caps`

Podría almacenar topes o límites aplicables a promociones, por ejemplo:

- tope por transacción
- tope diario
- tope mensual

### `promotion_exclusions`

Podría registrar exclusiones específicas, restricciones o condiciones no cubiertas por el modelo base.

### `promotion_sources`

Podría guardar información sobre la fuente de origen de una promoción, como:

- sitio web
- PDF
- landing page
- campaña externa

### `scrape_runs`

Podría registrar cada corrida del proceso de scraping, incluyendo:

- fecha y hora
- fuente procesada
- estado
- errores
- cantidad de registros detectados o actualizados

## Criterio para agregar nuevas tablas

No se deben agregar tablas solo porque parecen posibles o útiles en teoría.

Una nueva tabla debería incorporarse cuando ocurra al menos una de estas situaciones:

- existe una necesidad funcional real
- una estructura actual empieza a volverse ambigua o insuficiente
- el scraping requiere trazabilidad o normalización adicional
- una relación muchos a muchos ya no puede resolverse de forma simple

## Principio de diseño

Este documento no representa un compromiso definitivo de implementación. Su propósito es orientar la evolución del esquema manteniendo la primera etapa enfocada y controlada.

## Relación con la primera migración

Las tablas listadas aquí quedan explícitamente fuera de la primera migración documentada en:

- `docs/database/first-migration-scope.md`