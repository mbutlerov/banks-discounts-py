"""Stable merchant identities and source-backed offer participation.

PromotionOffer remains the source observation. MerchantOfferRule holds a shared,
location-independent rule payload so source keys, corrections and history survive
normalization.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.database.models.bank import Bank
    from app.database.models.merchant_group import MerchantGroup
    from app.database.models.merchant_location import MerchantLocation


class MerchantAlias(Base):
    __tablename__ = "merchant_aliases"
    __table_args__ = (
        UniqueConstraint("namespace", "source_key", name="uq_merchant_aliases_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    source_key: Mapped[str] = mapped_column(String(500), nullable=False)
    merchant_group_id: Mapped[int] = mapped_column(
        ForeignKey("merchant_groups.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    evidence_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    merchant_group: Mapped["MerchantGroup"] = relationship("MerchantGroup")


class MerchantLocationAlias(Base):
    __tablename__ = "merchant_location_aliases"
    __table_args__ = (
        UniqueConstraint("namespace", "source_key", name="uq_merchant_location_aliases_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    source_key: Mapped[str] = mapped_column(String(500), nullable=False)
    location_id: Mapped[int] = mapped_column(
        ForeignKey("merchant_locations.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    evidence_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    location: Mapped["MerchantLocation"] = relationship("MerchantLocation")


class MerchantOfferRule(Base):
    __tablename__ = "merchant_offer_rules"
    __table_args__ = (
        UniqueConstraint(
            "merchant_group_id", "bank_id", "context_key", "fingerprint",
            name="uq_merchant_offer_rules_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    merchant_group_id: Mapped[int] = mapped_column(
        ForeignKey("merchant_groups.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    bank_id: Mapped[int] = mapped_column(ForeignKey("banks.id", ondelete="CASCADE"), nullable=False, index=True)
    context_key: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    data_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    merchant_group: Mapped["MerchantGroup"] = relationship("MerchantGroup")
    bank: Mapped["Bank"] = relationship("Bank")


class OfferLocation(Base):
    __tablename__ = "offer_locations"
    __table_args__ = (
        CheckConstraint("publication IN ('confirmed', 'pending', 'retired')", name="ck_offer_locations_publication"),
    )

    offer_id: Mapped[int] = mapped_column(ForeignKey("promotion_offers.id", ondelete="CASCADE"), primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("merchant_locations.id", ondelete="CASCADE"), primary_key=True, index=True)
    publication: Mapped[str] = mapped_column(
        String(20), nullable=False, default="confirmed", server_default="confirmed",
    )
    data_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    location: Mapped["MerchantLocation"] = relationship("MerchantLocation")
