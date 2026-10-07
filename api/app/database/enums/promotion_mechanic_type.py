from enum import Enum


class PromotionMechanicType(str, Enum):
    INSTANT_DISCOUNT = "instant_discount"
    STATEMENT_CREDIT = "statement_credit"
    POINTS_ACCRUAL = "points_accrual"
    INSTALLMENTS = "installments"
    COMBINED = "combined"