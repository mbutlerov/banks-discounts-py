# ADR 004: Ubicación de enums en la capa de base de datos

## Estado

Aceptado

## Contexto

Durante la definición inicial del proyecto surgió la necesidad de organizar los enums utilizados por los modelos y por la persistencia.

En esta etapa, esos enums están fuertemente ligados al almacenamiento y a la representación de valores persistidos en base de datos. Ejemplos típicos pueden ser estados, tipos, fuentes o clasificaciones que terminan reflejándose directamente en columnas o restricciones del esquema.

Se evaluó dónde ubicarlos para mantener claridad sin introducir una separación artificial prematura.

## Decisión

Se decidió ubicar los enums en:

- `api/app/database/enums/`

## Consecuencias

### Positivas

- La ubicación refleja bien su uso actual.
- Se mantiene cerca de la capa con la que hoy tienen mayor relación.
- La estructura es clara para una etapa inicial.
- Evita mover conceptos al dominio antes de que exista una necesidad real.

### Negativas

- Algunos enums podrían necesitar reubicación futura si pasan a tener significado más amplio dentro del dominio.
- Si el proyecto evoluciona hacia una separación más estricta entre dominio e infraestructura, esta decisión puede requerir revisión.

## Justificación

En esta etapa del proyecto, estos enums están más cerca de la persistencia que de una lógica de dominio rica e independiente. Por eso, ubicarlos en la capa de base de datos es una decisión razonable, coherente y fácil de entender.

La documentación debe reflejar la realidad actual del proyecto, no una arquitectura idealizada todavía no necesaria.

## Alternativas consideradas

### 1. Ubicarlos en una carpeta global compartida

Se descartó por ser demasiado genérica para el uso real que tienen hoy.

### 2. Ubicarlos directamente en una capa de dominio

Se descartó en esta etapa porque todavía no todos los enums justifican ese nivel de abstracción.

## Revisión futura

Esta decisión puede revisarse si en el futuro:

- los enums empiezan a ser usados por múltiples capas
- dejan de estar atados a persistencia
- el dominio adquiere una estructura más marcada

## Notas

Esta decisión aplica a la etapa actual del proyecto y no impide una reorganización posterior si el sistema crece.