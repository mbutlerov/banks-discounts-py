from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.database.models.merchant_group import MerchantGroup
    from app.database.models.merchant_membership import MerchantOfferRule, OfferLocation
    from app.database.models.promotion import Promotion


class PromotionOffer(Base):
    __tablename__ = "promotion_offers"
    __table_args__ = (
        UniqueConstraint("promotion_id", "key", name="uq_promotion_offers_key"),
        CheckConstraint("location_scope IN ('unknown', 'specified', 'all')", name="ck_promotion_offers_location_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    promotion_id: Mapped[int] = mapped_column(ForeignKey("promotions.id", ondelete="CASCADE"), index=True)
    source_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    merchant_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchant_groups.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("merchant_offer_rules.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    location_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown",
    )
    key: Mapped[str] = mapped_column(String(240), nullable=False)
    data_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    publication: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    coverage_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    coverage_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    occurrences_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    promotion: Mapped["Promotion"] = relationship("Promotion", back_populates="offers")
    occurrences: Mapped[list["OfferOccurrence"]] = relationship("OfferOccurrence", cascade="all, delete-orphan")
    merchant_group: Mapped["MerchantGroup | None"] = relationship("MerchantGroup")
    rule: Mapped["MerchantOfferRule | None"] = relationship("MerchantOfferRule")
    location_memberships: Mapped[list["OfferLocation"]] = relationship(
        "OfferLocation", cascade="all, delete-orphan", passive_deletes=True,
    )


class OfferOccurrence(Base):
    __tablename__ = "offer_occurrences"
    __table_args__ = (Index("ix_offer_occurrences_date_offer", "applies_on", "offer_id"),)

    offer_id: Mapped[int] = mapped_column(ForeignKey("promotion_offers.id", ondelete="CASCADE"), primary_key=True)
    applies_on: Mapped[date] = mapped_column(Date, primary_key=True)
    rules_version: Mapped[int] = mapped_column(Integer, nullable=False)
