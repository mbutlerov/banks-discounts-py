"""Shared adapter identifiers; importing this module never loads configuration."""
from typing import Literal, get_args

BankSlug = Literal["ueno", "sudameris", "itau", "atlas", "gnb"]
BANKS: tuple[BankSlug, ...] = get_args(BankSlug)
ADAPTER_VERSION = "canonical-v4"
