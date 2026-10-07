from __future__ import annotations
from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.enums.card_product_type import CardProductType

if TYPE_CHECKING:
    from app.database.models.bank import Bank
    from app.database.models.card_brand import CardBrand

class CardProduct(Base):
    __tablename__ = "card_products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    bank_id: Mapped[int] = mapped_column(
        ForeignKey("banks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    card_brand_id: Mapped[int | None] = mapped_column(
        ForeignKey("card_brands.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    product_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=CardProductType.CREDIT.value,
    )
    segment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

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

    bank: Mapped["Bank"] = relationship("Bank", back_populates="card_products")
    card_brand: Mapped["CardBrand | None"] = relationship("CardBrand", back_populates="card_products")