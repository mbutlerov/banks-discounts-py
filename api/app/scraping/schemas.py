from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.promotions.schemas import OfferData


@dataclass(slots=True)
class ScrapedSource:
    source_type: str
    source_url: str
    content: bytes | None = None
    text: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    # Sources acquire attachments; parsers consume them without network access.
    documents: list[ScrapedSource] = field(default_factory=list)

@dataclass(slots=True)
class ScrapedPage:
    page_number: int
    text: str
    metadata: dict[str, str] = field(default_factory=dict)

@dataclass(slots=True)
class ScrapedPromotion:
    bank_slug: str
    title: str
    description: str | None = None
    category_name: str | None = None
    merchant_name: str | None = None
    campaign_name: str | None = None
    benefit_type: str | None = None
    mechanic_type: str | None = None
    discount_percentage: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    terms_summary: str | None = None
    raw_text: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    source_key: str | None = None
    offers: list[OfferData] = field(default_factory=list)
