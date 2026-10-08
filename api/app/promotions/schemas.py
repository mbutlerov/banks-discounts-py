from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CanonicalModel(BaseModel):
    # Responses include defaults and nulls. OpenAPI must describe the actual
    # serialized shape so generated clients do not treat present fields as absent.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


class Evidence(CanonicalModel):
    source_url: str | None = None
    document_id: int | None = None
    field: str | None = None
    text: str = ""
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    method: str = "deterministic"


class MerchantIdentity(CanonicalModel):
    """An adapter-supplied, source-backed identity, independent of display names."""

    namespace: str = Field(min_length=1, max_length=100)
    key: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=255)
    evidence: list[Evidence] = Field(default_factory=list)


class MerchantLocation(CanonicalModel):
    key: str = Field(min_length=1, max_length=240)
    name: str = Field(min_length=1, max_length=255)
    city: str | None = None
    address: str | None = None
    channels: list[str] = Field(default_factory=list)
    processors: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class PromotionLocation(MerchantLocation):
    variant_keys: list[str] = Field(default_factory=list)
    source_url: str | None = None
    source_page: int | None = Field(default=None, ge=1)


class PromotionGrouping(CanonicalModel):
    key: str
    name: str
    kind: Literal["chain"] = "chain"


class Schedule(CanonicalModel):
    state: Literal["known", "unknown", "conflict"] = "unknown"
    kind: Literal["all_days", "weekly", "monthly_day", "monthly_nth_weekday", "specific_dates"] = "weekly"
    weekdays: list[int] = Field(default_factory=list)
    month_days: list[int] = Field(default_factory=list)
    ordinal: int | None = None
    dates: list[date] = Field(default_factory=list)
    exclusions: list[date] = Field(default_factory=list)
    excluded_weekdays: list[int] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule(self) -> Schedule:
        if any(day < 1 or day > 7 for day in self.weekdays + self.excluded_weekdays):
            raise ValueError("weekdays must use ISO values 1..7")
        if any(day < 1 or day > 31 for day in self.month_days):
            raise ValueError("month_days must be within 1..31")
        if self.ordinal is not None and self.ordinal not in (-1, 1, 2, 3, 4, 5):
            raise ValueError("ordinal must be -1 (last) or 1..5")
        if self.state == "known":
            if self.kind == "weekly" and not self.weekdays:
                raise ValueError("known weekly rules require weekdays")
            if self.kind == "monthly_day" and not self.month_days:
                raise ValueError("known monthly rules require month_days")
            if self.kind == "monthly_nth_weekday" and (len(self.weekdays) != 1 or self.ordinal is None):
                raise ValueError("ordinal rules require one weekday and ordinal")
            if self.kind == "specific_dates" and not self.dates:
                raise ValueError("known specific-date rules require dates")
        return self


class Benefit(CanonicalModel):
    type: Literal["discount", "cashback", "installments", "points", "other"]
    percentage: Decimal | None = Field(default=None, ge=0, le=100)
    installments: int | None = Field(default=None, ge=1, le=120)
    label: str | None = None
    is_maximum: bool = False
    conditions: list[str] = Field(default_factory=list)


class Eligibility(CanonicalModel):
    cards: list[str] = Field(default_factory=list)
    card_types: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    processors: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    personalization_required: bool = False
    unknown_fields: list[str] = Field(default_factory=list)


class Cap(CanonicalModel):
    type: Literal["purchase", "cashback", "discount", "other"] = "cashback"
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str = "PYG"
    period: Literal["transaction", "day", "week", "month", "campaign", "unknown"] = "unknown"
    scope: Literal["card", "customer", "account", "merchant", "unknown"] = "unknown"
    description: str | None = None


class OfferData(CanonicalModel):
    schema_version: int = 1
    key: str = Field(min_length=1, max_length=240)
    merchant_name: str = Field(min_length=1, max_length=255)
    merchant: MerchantIdentity | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    validity_state: Literal["known", "unknown", "conflict"] = "unknown"
    start_open: bool = False
    end_open: bool = False
    schedule: Schedule = Field(default_factory=Schedule)
    benefits: list[Benefit] = Field(default_factory=list)
    eligibility: Eligibility = Field(default_factory=Eligibility)
    caps: list[Cap] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    publication: Literal["confirmed", "pending", "retired"] = "pending"
    terms: list[str] = Field(default_factory=list)
    source_url: str | None = None
    locations: list[MerchantLocation] = Field(default_factory=list)
    location_scope: Literal["unknown", "specified", "all"] = "unknown"

    @model_validator(mode="after")
    def validate_dates(self) -> OfferData:
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError("valid_from must not be after valid_until")
        if self.validity_state == "known":
            if self.valid_from is None and not self.start_open:
                raise ValueError("missing valid_from requires explicit start_open")
            if self.valid_until is None and not self.end_open:
                raise ValueError("missing valid_until requires explicit end_open")
        return self


class CatalogItem(CanonicalModel):
    slug: str
    name: str


class BankCatalogItem(CatalogItem):
    data_status: Literal["updated", "partial", "unavailable", "never"] = "never"
    last_attempt_at: datetime | None = None
    last_updated_at: datetime | None = None


class OfferResponse(OfferData):
    matched_dates: list[date] = Field(default_factory=list)
    availability: Literal["confirmed", "unknown", "conflict", "not_applicable"] = "unknown"


class PromotionResponse(CanonicalModel):
    slug: str
    title: str
    merchant_name: str | None = None
    short_description: str | None = None
    description: str | None = None
    benefit_type: str | None = None
    discount_percentage: Decimal | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: str
    pdf_url: str | None = None
    source_url: str | None = None
    valid_days: list[str] | None = None
    installments: int | None = None
    applicable_cards: list[str] | None = None
    category: CatalogItem | None = None
    bank: CatalogItem
    matched_dates: list[date] = Field(default_factory=list)
    variants: list[OfferResponse] = Field(default_factory=list)
    availability: Literal["confirmed", "unknown", "conflict", "not_applicable"] = "unknown"
    last_checked_at: datetime | None = None
    terms_summary: str | None = None
    grouping: PromotionGrouping | None = None
    locations: list[PromotionLocation] = Field(default_factory=list)
    location_scope: Literal["unknown", "specified", "all"] = "unknown"


class PromotionsResponse(CanonicalModel):
    total: int
    page: int
    size: int
    date_from: date
    date_to: date
    timezone: str = "America/Asuncion"
    items: list[PromotionResponse]
