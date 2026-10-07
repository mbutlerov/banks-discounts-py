from __future__ import annotations
from typing import TYPE_CHECKING

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.enums.campaign_type import CampaignType

if TYPE_CHECKING:
    from app.database.models.bank import Bank
    from app.database.models.promotion import Promotion

class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    bank_id: Mapped[int] = mapped_column(
        ForeignKey("banks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    slug: Mapped[str] = mapped_column(String(150), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    campaign_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        default=CampaignType.MONTHLY.value,
    )
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

    bank: Mapped["Bank"] = relationship("Bank", back_populates="campaigns")
    promotions: Mapped[list["Promotion"]] = relationship(
        "Promotion",
        back_populates="campaign",
    )