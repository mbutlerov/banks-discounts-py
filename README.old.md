# 🏦 Banks Discounts API

Backend para agregación y consulta de promociones bancarias.

## 🧰 Stack

- FastAPI
- PostgreSQL 16
- SQLAlchemy 2
- Alembic
- Docker Compose (base + dev + prod)

## 🐳 Levantar el Proyecto

### Dev

```bash
docker compose 
  -p banks-dev \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up -d --build
```

#### Accesos
    Api -> http://localhost:8000
    DB -> localhost:5432

#### Para ver los logs
```bash
docker compose -p banks-dev logs -f api
```

#### Bajar entorno
```bash
docker compose -p banks-dev down
```

#### Borrar base de datos
```bash
docker compose -p banks-dev down -v
```


### Migraciones (Alembic)

#### Generar migracion:
```bash
docker compose -p banks-dev exec api \
  sh -lc "cd /app && alembic revision --autogenerate -m 'mensaje'"
```

#### Aplicar migraciones:
```bash
docker compose -p banks-dev exec api \
  sh -lc "cd /app && alembic upgrade head"
```

#### Ver version actual:
```bash
docker compose -p banks-dev exec api \
  sh -lc "cd /app && alembic current"
```