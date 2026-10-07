"""Serve the real API against a temporary, migrated PostgreSQL test schema.

No bank requests, production credentials or existing application data are used.
Run from api/: TEST_DATABASE_URL=... python -m scripts.contract_server --port 8767
"""
from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Bank, Category, ScrapeRun
from app.database.session import get_db
from app.main import create_app
from app.promotions.schemas import Benefit, Cap, Eligibility, Evidence, MerchantIdentity, MerchantLocation, OfferData, Schedule
from app.scraping.persistence import save_promotions
from app.scraping.schemas import ScrapedPromotion

CONTRACT_DATE = date(2026, 10, 5)
API_DIR = Path(__file__).resolve().parents[1]


def require_test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or "test" not in (make_url(url).database or "").lower():
        raise ValueError("Contract integration requires TEST_DATABASE_URL pointing to an explicitly named test database.")
    return url


def seed_contract(db: Session) -> None:
    """Representative wire shapes, separate from scraped/public production data."""
    from app.scraping.registry import ADAPTER_VERSION

    db.add(Bank(slug="ueno", name="ueno bank"))
    db.add(Category(slug="supermarket", name="Supermercados"))
    db.flush()
    url = "https://www.ueno.com.py/contract-test"
    evidence = Evidence(source_url=url, page=1, section="contract-fixture", text="Controlled contract test data.")
    merchant = MerchantIdentity(namespace="contract:merchants", key="market", name="Contract Market", evidence=[evidence])
    locations = [
        MerchantLocation(key="asuncion", name="Contract Market Centro", city="Asunción", address="Calle Uno 100", evidence=[evidence]),
        MerchantLocation(key="luque", name="Contract Market Luque", city="Luque", address="Calle Dos 200", evidence=[evidence]),
    ]
    offers = [OfferData(
        key=key, merchant_name=merchant.name, merchant=merchant,
        valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 31), validity_state="known",
        schedule=Schedule(state="known", kind="all_days"),
        benefits=[Benefit(type="cashback", percentage=percentage)],
        eligibility=Eligibility(cards=[card], card_types=["credit"], channels=["POS"]),
        caps=[Cap(amount=100000, period="month", scope="card")],
        evidence=[evidence], source_url=url, publication="confirmed",
        locations=locations, location_scope="specified",
    ) for key, percentage, card in [("standard", 20, "Visa Clásica"), ("premium", 30, "Visa Infinite")]]
    confirmed = ScrapedPromotion(
        bank_slug="ueno", title="Contract Market", source_key="contract:market",
        category_name="Supermercados", merchant_name=merchant.name,
        metadata={"source_url": url, "offers_complete": "true"}, offers=offers,
    )
    pending = ScrapedPromotion(
        bank_slug="ueno", title="Contract Pending", source_key="contract:pending",
        category_name="Supermercados", merchant_name="Contract Pending",
        metadata={"source_url": url + "/pending"},
        offers=[OfferData(key="pending", merchant_name="Contract Pending", publication="pending",
                          benefits=[Benefit(type="discount", percentage=10)])],
    )
    save_promotions(db, [confirmed, pending], commit=False)
    db.add(ScrapeRun(bank_slug="ueno", state="success", adapter_version=ADAPTER_VERSION,
                    finished_at=datetime.now(timezone.utc)))
    db.commit()


@asynccontextmanager
async def contract_lifespan(application):
    engine = sa.create_engine(require_test_database_url(), pool_pre_ping=True)
    schema = "contract_" + uuid4().hex
    created = False
    previous = application.dependency_overrides.get(get_db)
    try:
        with engine.begin() as connection:
            connection.execute(sa.schema.CreateSchema(schema))
        created = True
        with engine.begin() as connection:
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config(str(API_DIR / "alembic.ini"))
            config.set_main_option("script_location", str(API_DIR / "alembic"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        isolated = engine.execution_options(schema_translate_map={None: schema})
        sessions = sessionmaker(bind=isolated)
        with sessions() as db:
            seed_contract(db)

        def contract_db():
            with sessions() as db:
                yield db

        application.dependency_overrides[get_db] = contract_db
        yield
    finally:
        if previous is None:
            application.dependency_overrides.pop(get_db, None)
        else:
            application.dependency_overrides[get_db] = previous
        if created:
            with engine.begin() as connection:
                connection.execute(sa.schema.DropSchema(schema, cascade=True))
        engine.dispose()


def contract_app():
    # Retain the same routes, middleware, models and serializers as app.main.
    application = create_app()
    application.router.lifespan_context = contract_lifespan
    return application


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    require_test_database_url()
    uvicorn.run(contract_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
