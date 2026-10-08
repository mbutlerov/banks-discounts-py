from enum import Enum


class PromotionBenefitType(str, Enum):
    DISCOUNT = "discount"
    CASHBACK = "cashback"
    INSTALLMENTS = "installments"
    POINTS = "points"
    MIXED = "mixed"