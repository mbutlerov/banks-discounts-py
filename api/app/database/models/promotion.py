from __future__ import annotations
from typing import TYPE_CHECKING

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.enums.promotion_benefit_type import PromotionBenefitType
from app.database.enums.promotion_mechanic_type import PromotionMechanicType
from app.database.enums.promotion_status import PromotionStatus

if TYPE_CHECKING:
    from app.database.models.bank import Bank
    from app.database.models.campaign import Campaign
    from app.database.models.category import Category
    from app.database.models.offer import PromotionOffer

class Promotion(Base):
    __tablename__ = "promotions"
    __table_args__ = (UniqueConstraint("bank_id", "source_key", name="uq_promotions_bank_source_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    bank_id: Mapped[int] = mapped_column(
        ForeignKey("banks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True, nullable=False)
    source_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    publication: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    short_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    benefit_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        default=PromotionBenefitType.DISCOUNT.value,
    )
    mechanic_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        default=PromotionMechanicType.INSTANT_DISCOUNT.value,
    )

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=PromotionStatus.DRAFT.value,
    )

    applies_to_all_locations: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    is_cumulative: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    discount_percentage: Mapped[int | None] = mapped_column(nullable=True)
    terms_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    bank: Mapped["Bank"] = relationship("Bank", back_populates="promotions")
    campaign: Mapped["Campaign | None"] = relationship("Campaign", back_populates="promotions")
    category: Mapped["Category | None"] = relationship("Category", back_populates="promotions")
    offers: Mapped[list["PromotionOffer"]] = relationship("PromotionOffer", back_populates="promotion", cascade="all, delete-orphan")
