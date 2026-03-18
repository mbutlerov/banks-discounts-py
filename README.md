# 🏦 Banks Discounts API

Backend para agregación y consulta de promociones bancarias.

## 🧰 Stack

- FastAPI
- PostgreSQL 16
- SQLAlchemy 2
- Alembic
- Docker Compose

---

## 🚀 Quick start

### Levantar entorno de desarrollo

```bash
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

### Ver logs de la API

```bash
docker compose -p banks-dev logs -f api
```

### Accesos

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Healthcheck: `http://localhost:8000/api/v1/health`
- PostgreSQL: `localhost:5432`

### Bajar entorno

```bash
docker compose -p banks-dev down
```

### Borrar base de datos

```bash
docker compose -p banks-dev down -v
```

> `down` no borra datos.  
> `down -v` sí borra el volumen de Postgres.

---

## 📁 Estructura del proyecto

```text
.
├── api/
│   ├── app/
│   │   ├── api/
│   │   │   └── v1/
│   │   │       └── routes/
│   │   ├── core/
│   │   ├── database/
│   │   │   ├── base.py
│   │   │   ├── models.py
│   │   │   └── session.py
│   │   └── main.py
│   ├── alembic/
│   ├── environments/
│   │   ├── dev.env
│   │   ├── qa.env
│   │   └── prod.env
│   ├── alembic.ini
│   ├── Dockerfile
│   └── pyproject.toml
├── docker-compose.yml
├── docker-compose.dev.yml
├── docker-compose.prod.yml
└── README.md
```

---

## ⚙️ Variables de entorno

### `api/environments/dev.env`

```env
ENV=dev
APP_VERSION=0.1.0

DB_HOST=db
DB_PORT=5432
DB_NAME=dev_banks_discounts
DB_USER=postgres
DB_PASSWORD=postgres
DB_SSLMODE=disable
```

### `api/environments/prod.env`

```env
ENV=prod
APP_VERSION=0.1.0

DB_HOST=db
DB_PORT=5432
DB_NAME=prod_banks_discounts
DB_USER=postgres
DB_PASSWORD=postgres
DB_SSLMODE=disable
```

### `api/environments/qa.env`

```env
ENV=qa
APP_VERSION=0.1.0

DB_HOST=db
DB_PORT=5432
DB_NAME=qa_banks_discounts
DB_USER=postgres
DB_PASSWORD=postgres
DB_SSLMODE=disable
```

---

## 🐳 Docker Compose

### `docker-compose.yml`
Archivo base con la configuración común de los servicios.

Incluye:
- servicio `db`
- servicio `api`
- volumen persistente para PostgreSQL
- `healthcheck`
- build del backend

### `docker-compose.dev.yml`
Override para desarrollo.

Incluye:
- puertos expuestos
- `dev.env`
- volume del código para hot reload
- `WATCHFILES_FORCE_POLLING=true`
- `uvicorn --reload`

### `docker-compose.prod.yml`
Override para producción.

Incluye:
- `prod.env`
- sin reload
- sin volume del código
- API expuesta internamente con `expose`

---

## 🛠️ Comandos por entorno

### Desarrollo

```bash
docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

### Producción

```bash
docker compose -p banks-prod -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### Ver logs en producción

```bash
docker compose -p banks-prod logs -f api
```

---

## 🗄️ Base de datos

### Entrar a PostgreSQL en desarrollo

```bash
docker compose -p banks-dev exec db psql -U postgres -d dev_banks_discounts
```

### Listar tablas

```sql
\dt
```

### Salir

```sql
\q
```

---

## 🔁 Migraciones con Alembic

### Generar una migración nueva

```bash
docker compose -p banks-dev exec api sh -lc "cd /app && alembic revision --autogenerate -m 'descripcion'"
```

### Aplicar migraciones

```bash
docker compose -p banks-dev exec api sh -lc "cd /app && alembic upgrade head"
```

### Ver la migración actual

```bash
docker compose -p banks-dev exec api sh -lc "cd /app && alembic current"
```

---

## 🧱 Cómo crear una tabla nueva

### 1. Definir el modelo en `api/app/database/models.py`

Ejemplo:

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
```

### 2. Verificar que Alembic detecte los modelos

En `api/alembic/env.py` debe estar importado el módulo de modelos y `target_metadata` debe apuntar a `Base.metadata`.

Ejemplo:

```python
from app.database.base import Base
from app.database import models

target_metadata = Base.metadata
```

### 3. Generar la migración

```bash
docker compose -p banks-dev exec api sh -lc "cd /app && alembic revision --autogenerate -m 'add merchants table'"
```

### 4. Aplicar la migración

```bash
docker compose -p banks-dev exec api sh -lc "cd /app && alembic upgrade head"
```

### 5. Verificar que la tabla exista

```bash
docker compose -p banks-dev exec db psql -U postgres -d dev_banks_discounts -c "\dt"
```

---

## ❤️ Healthcheck

Endpoint:

```text
GET /api/v1/health
```

Ejemplo:

```bash
curl http://localhost:8000/api/v1/health
```

Respuesta esperada:

```json
{
  "status": "healthy",
  "environment": "dev",
  "app_version": "0.1.0",
  "db": "ok"
}
```

---

## 🧠 Flujo de desarrollo recomendado

1. Modificar o crear modelos en `api/app/database/models.py`
2. Generar migración con Alembic
3. Aplicar migración
4. Probar endpoint o comportamiento asociado
5. Commit del cambio

---

## 📌 Notas importantes

- En desarrollo, la autorecarga funciona solo levantando con `docker-compose.dev.yml`
- `WATCHFILES_FORCE_POLLING=true` ayuda a que el reload funcione correctamente en Windows
- No usar `Base.metadata.create_all()` en este proyecto
- La forma correcta de versionar cambios de esquema es con Alembic
- Si Alembic queda desincronizado con la base, revisar la tabla `alembic_version`

---

