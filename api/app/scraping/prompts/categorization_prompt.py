"""Compatibility imports for experimental categorization resources."""
from app.scraping.experimental.prompts.categorization_prompt import (
    CATEGORIZATION_SCHEMA,
    CATEGORY_SLUGS,
    build_categorization_prompt,
)

__all__ = ["CATEGORIZATION_SCHEMA", "CATEGORY_SLUGS", "build_categorization_prompt"]
