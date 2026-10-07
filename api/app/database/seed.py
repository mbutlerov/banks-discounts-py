"""Script de seed inicial. Ejecutar una sola vez después de aplicar las migraciones.

Uso:
    docker compose -p banks-dev -f docker-compose.yml -f docker-compose.dev.yml \
        exec api python -m app.database.seed
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.database.models.bank import Bank
from app.database.models.category import Category
from app.database.session import SessionLocal

logger = logging.getLogger(__name__)

BANKS = [
    {
        "slug": "atlas",
        "name": "Banco Atlas",
        "website_url": "https://www.bancoatlas.com.py",
        "country_code": "PY",
        "is_active": True,
    },
    {
        "slug": "gnb",
        "name": "Banco GNB Paraguay",
        "website_url": "https://www.bancognb.com.py",
        "country_code": "PY",
        "is_active": True,
    },
    {
        "slug": "ueno",
        "name": "ueno bank",
        "website_url": "https://www.ueno.com.py",
        "country_code": "PY",
        "is_active": True,
    },
    {
        "slug": "sudameris",
        "name": "Banco Sudameris",
        "website_url": "https://www.sudameris.com.py",
        "country_code": "PY",
        "is_active": True,
    },
    {
        "slug": "itau",
        "name": "Banco Itaú Paraguay",
        "website_url": "https://www.itau.com.py",
        "country_code": "PY",
        "is_active": True,
    },
]

CATEGORIES = [
    {"slug": "gastronomy",    "name": "Gastronomía"},
    {"slug": "fuel",          "name": "Combustibles"},
    {"slug": "supermarket",   "name": "Supermercados"},
    {"slug": "pharmacy",      "name": "Farmacias"},
    {"slug": "fashion",       "name": "Indumentaria"},
    {"slug": "travel",        "name": "Viajes & Turismo"},
    {"slug": "entertainment", "name": "Entretenimiento"},
    {"slug": "health",        "name": "Salud & Bienestar"},
    {"slug": "home",          "name": "Hogar"},
    {"slug": "technology",    "name": "Tecnología"},
    {"slug": "other",         "name": "Otros"},
]


def seed_banks(db: Session) -> None:
    for data in BANKS:
        if not db.query(Bank).filter_by(slug=data["slug"]).first():
            db.add(Bank(**data))
            logger.info("Banco '%s' agregado.", data["slug"])
        else:
            logger.info("Banco '%s' ya existe, saltando.", data["slug"])
    db.commit()
    logger.info("Seed de bancos completado.")


def seed_categories(db: Session) -> None:
    for data in CATEGORIES:
        if not db.query(Category).filter_by(slug=data["slug"]).first():
            db.add(Category(**data, is_active=True))
            logger.info("Categoría '%s' agregada.", data["slug"])
        else:
            logger.info("Categoría '%s' ya existe, saltando.", data["slug"])
    db.commit()
    logger.info("Seed de categorías completado.")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    db = SessionLocal()
    try:
        seed_banks(db)
        seed_categories(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
