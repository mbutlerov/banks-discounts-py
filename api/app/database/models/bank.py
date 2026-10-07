from __future__ import annotations
from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.database.models.card_product import CardProduct
    from app.database.models.campaign import Campaign
    from app.database.models.promotion import Promotion


class Bank(Base):
    __tablename__ = "banks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="PY")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    card_products: Mapped[list["CardProduct"]] = relationship(
        "CardProduct",
        back_populates="bank",
        cascade="all, delete-orphan",
    )
    campaigns: Mapped[list["Campaign"]] = relationship(
        "Campaign",
        back_populates="bank",
        cascade="all, delete-orphan",
    )
    promotions: Mapped[list["Promotion"]] = relationship(
        "Promotion",
        back_populates="bank",
        cascade="all, delete-orphan",
    )