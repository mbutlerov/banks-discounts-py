from enum import Enum


class CardProductType(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    PREPAID = "prepaid"
    QR_CREDIT = "qr_credit"