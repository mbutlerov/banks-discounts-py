# ADR 005: ofertas, calendario único y scraping fuera de la consulta

Fecha: 3 de octubre de 2026. Estado: **implementado**.

## Contexto

Un PDF o campaña bancaria puede combinar comercios, días y variantes de tarjeta. Reducirlo a un porcentaje y una lista de días permite anunciar beneficios que no corresponden a la misma condición. El estado `active` calculado durante una corrida también queda desactualizado cuando cambian las fechas.

La web debe responder qué beneficio concreto aplica en una fecha o fin de semana. La ejecución del scraper necesita conservar errores y evidencia, sobrevivir reinicios y permitir investigar documentos sin volver a descargar el catálogo.

## Decisión

Conservar FastAPI, PostgreSQL, Next.js y un adaptador específico para cada banco. Separar campaña de oferta: `promotions` mantiene identidad/slug de origen y `promotion_offers` guarda comercio y variante, con beneficios, elegibilidad, topes, vigencia, regla y evidencia validados por Pydantic.

Usar un único evaluador puro para recurrencias y exclusiones. Los estados desconocido y conflicto nunca confirman disponibilidad. Los límites abiertos requieren declaración explícita; ausencia de límite no significa vigencia infinita.

Materializar 90 días de ocurrencias por oferta y versión. Reemplazar índice y cobertura en la misma transacción. La consulta usa el índice para reducir candidatos cuando es completo y recurre al evaluador si está desactualizado, fuera de cobertura o tiene correcciones. Aplicar todos los filtros a la misma variante antes de agrupar, contar y paginar. Limitar intervalos públicos a 31 días.

Ejecutar scraping con CLI y un worker independiente. PostgreSQL guarda corridas y cola durables; un lock por banco impide ejecución simultánea. Los endpoints administrativos requieren autenticación. Los antiguos GET con efectos de escritura quedan retirados.

Guardar bytes originales con hash y snapshots atómicos. Registrar documentos en una transacción independiente del candidato extraído. El replay solamente usa los documentos guardados y falla ante un faltante. No retirar promociones por ausencia en una corrida parcial o fallida; la retirada automática permanece deshabilitada.

Aplicar correcciones manuales como una superposición validada al dato fuente. Conservar identidad estable y observaciones válidas ante una extracción incompleta. Adoptar bases antiguas mediante una baseline congelada y cambios aditivos; bloquear el downgrade histórico destructivo.

## Consecuencias

- La interfaz puede mostrar fechas reales y condiciones de cada variante, sin mezclar beneficios de tarjetas diferentes.
- El índice es una optimización regenerable; la disponibilidad no depende de que una fecha esté materializada.
- Los datos incompletos dejan de aparecer como descuentos confirmados. Su cobertura es visible mediante el filtro de pendientes.
- JSONB conserva un contrato validado y versionado; los campos de tarjeta/comercio todavía no se reconcilian con todos los catálogos relacionales.
- La consulta puede evaluar candidatos en Python cuando no hay cobertura. Antes de crecer sustancialmente deben medirse esos casos; no se pagina el catálogo para después filtrar.
- No se introduce una cola externa. Un worker y PostgreSQL son suficientes para la operación actual; el scheduler no se duplica dentro de cada proceso Uvicorn.
- El retiro automático por faltantes exige una política de cobertura y revisión adicional. No está activado por defecto.

## Verificación

Las pruebas cubren reglas mensuales/semanales, exclusiones, desconocidos, combinación correcta de condiciones, agrupación/paginación, índices viejos o fuera de cobertura, persistencia idempotente, correcciones, cola autenticada, locks, snapshots después de rollback, replay sin red y las tres rutas de migración.

Detalle del esquema: [schema-overview.md](../database/schema-overview.md).
