from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models.bank import Bank
from app.database.models.category import Category
from app.database.models.ingestion import ScrapeRun
from app.database.session import get_db
from app.promotions.schemas import BankCatalogItem, CatalogItem

router = APIRouter(prefix="/catalogs", tags=["catalogs"])


@router.get("/banks", response_model=list[BankCatalogItem])
def banks(db: Session = Depends(get_db)) -> list[BankCatalogItem]:
    active = db.query(Bank).filter(Bank.is_active.is_(True)).order_by(Bank.name, Bank.id).all()
    if not active:
        return []
    slugs = [row.slug for row in active]
    completed = db.query(
        ScrapeRun.bank_slug, ScrapeRun.state, ScrapeRun.finished_at,
        func.row_number().over(partition_by=ScrapeRun.bank_slug, order_by=(ScrapeRun.finished_at.desc(), ScrapeRun.id.desc())).label("position"),
    ).filter(ScrapeRun.bank_slug.in_(slugs), ScrapeRun.state.in_(["success", "partial", "failed"]), ScrapeRun.finished_at.isnot(None)).subquery()
    latest = {row.bank_slug: row for row in db.query(completed).filter(completed.c.position == 1)}
    updated = {
        row.bank_slug: row.finished_at
        for row in db.query(ScrapeRun.bank_slug, func.max(ScrapeRun.finished_at).label("finished_at"))
        .filter(ScrapeRun.bank_slug.in_(slugs), ScrapeRun.state.in_(["success", "partial"]), ScrapeRun.finished_at.isnot(None))
        .group_by(ScrapeRun.bank_slug)
    }
    return [BankCatalogItem(
        slug=bank.slug, name=bank.name,
        data_status={"success": "updated", "partial": "partial", "failed": "unavailable"}[latest[bank.slug].state] if bank.slug in latest else "never",
        last_attempt_at=latest[bank.slug].finished_at if bank.slug in latest else None,
        last_updated_at=updated.get(bank.slug),
    ) for bank in active]


@router.get("/categories", response_model=list[CatalogItem])
def categories(db: Session = Depends(get_db)) -> list[CatalogItem]:
    return [CatalogItem(slug=row.slug, name=row.name) for row in db.query(Category).filter(Category.is_active.is_(True)).order_by(Category.name, Category.id)]
