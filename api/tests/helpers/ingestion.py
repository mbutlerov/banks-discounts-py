"""Ingestion candidate and PostgreSQL fixtures shared with AI proposal tests.

Each test gets a fresh schema in the explicitly named test database.
"""
import os
from datetime import date
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.models import Bank, Category
from app.promotions.schemas import Benefit, OfferData, Schedule
from app.scraping.schemas import ScrapedPromotion


@pytest.fixture
def ingestion_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to an isolated PostgreSQL test database")
    if "test" not in (make_url(url).database or "").lower():
        raise RuntimeError("Ingestion tests require an explicitly named test database")
    base_engine = sa.create_engine(url)
    schema = f"ingestion_test_{uuid4().hex}"
    with base_engine.begin() as connection:
        connection.execute(sa.schema.CreateSchema(schema))
    engine = base_engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Bank(slug="ueno", name="Ueno"))
        db.add(Category(slug="other", name="Otros"))
        db.commit()
    try:
        yield engine
    finally:
        with base_engine.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        base_engine.dispose()


@pytest.fixture
def db(ingestion_engine):
    with Session(ingestion_engine) as db:
        yield db


def candidate(title="Cafe", key="merchant:1", offer_key="standard", **changes):
    data = OfferData(key=offer_key, merchant_name=title, valid_from=date(2026, 10, 1), valid_until=date(2026, 10, 31), validity_state="known", schedule=Schedule(state="known", kind="weekly", weekdays=[6]), benefits=[Benefit(type="cashback", percentage=20)], publication="confirmed", source_url="https://www.ueno.com.py/example")
    fields = dict(bank_slug="ueno", title=title, source_key=key, description="Verified conditions", discount_percentage=20, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31), metadata={"source_url": "https://www.ueno.com.py/example", "applicable_cards": "Visa"}, offers=[data])
    fields.update(changes)
    return ScrapedPromotion(**fields)
