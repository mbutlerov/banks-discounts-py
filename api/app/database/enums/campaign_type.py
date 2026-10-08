from enum import Enum


class CampaignType(str, Enum):
    MONTHLY = "monthly"
    SEASONAL = "seasonal"
    SPECIAL_EVENT = "special_event"
    CATEGORY = "category"