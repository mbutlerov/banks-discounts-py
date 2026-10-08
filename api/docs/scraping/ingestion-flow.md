# Flujo de scraping e ingestión

## Objetivo

Este documento describe el flujo previsto para la recolección, procesamiento y persistencia de promociones bancarias desde fuentes externas.

No representa necesariamente una implementación cerrada, sino la arquitectura funcional esperada para esta parte del sistema.

## Flujo general

A alto nivel, el proceso de ingestión debería seguir estas etapas:

1. detección o programación de una corrida
2. obtención del contenido fuente
3. almacenamiento de la fuente cruda
4. validación o control de cambios
5. parsing y extracción estructurada
6. normalización de datos
7. persistencia o actualización en base de datos
8. registro de auditoría de la corrida

## Etapas previstas

### 1. Ejecución programada

El scraping idealmente se ejecutará de forma periódica, por ejemplo mediante un cron diario o un mecanismo equivalente.

El objetivo es revisar fuentes conocidas y detectar novedades o cambios.

### 2. Descarga de fuentes

Las fuentes pueden incluir:

- HTML
- PDF
- landing pages
- documentos promocionales publicados por bancos o comercios

### 3. Guardado de fuente cruda

Antes de parsear, conviene almacenar la respuesta original o una representación fiel de la fuente descargada.

Esto permite:

- auditoría
- reprocesamiento
- comparación ante errores
- trazabilidad

### 4. Checksum o control de cambios

Se prevé utilizar algún mecanismo de checksum o huella del contenido para detectar cambios entre corridas y evitar procesamiento innecesario cuando la fuente no cambió.

### 5. Parsing

Una vez obtenida la fuente, se extrae la información relevante de forma estructurada.

Según el tipo de fuente, el parser puede ser diferente.

Ejemplos:

- parseo HTML por selectores
- extracción desde PDF
- procesamiento especial para contenido no estructurado

### 6. Normalización

Los datos extraídos deben transformarse a un formato coherente con el modelo interno del sistema.

Esto puede incluir:

- limpieza de texto
- normalización de nombres
- homologación de categorías
- interpretación de fechas
- asociación con bancos, campañas o tipos de tarjeta

### 7. Upsert de promociones

Con los datos normalizados, el sistema debe poder:

- insertar nuevas promociones
- actualizar promociones existentes
- evitar duplicados
- mantener consistencia con campañas, ubicaciones y entidades relacionadas

### 8. Auditoría de corridas

Cada ejecución debería dejar trazabilidad suficiente para responder preguntas como:

- qué fuente se procesó
- cuándo se procesó
- si hubo error
- cuántos registros se detectaron
- cuántos se insertaron o actualizaron

## Consideraciones de diseño

### Idempotencia

El flujo debería diseñarse para que una misma fuente pueda reprocesarse sin generar duplicados innecesarios.

### Trazabilidad

Siempre que sea posible, debe conservarse vínculo entre el dato estructurado y su fuente original.

### Evolución por etapas

No es necesario implementar todo este flujo desde el inicio. Puede desarrollarse progresivamente, empezando por una o pocas fuentes y agregando componentes a medida que el sistema madure.

## Posibles extensiones futuras

- almacenamiento de snapshots de HTML o PDF
- cola de procesamiento
- validación manual de promociones ambiguas
- extracción asistida por IA para casos complejos
- métricas de calidad de scraping
- alertas ante cambios importantes en fuentes

## Documentos relacionados

- `docs/database/future-tables.md`