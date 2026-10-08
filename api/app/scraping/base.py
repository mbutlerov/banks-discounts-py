from __future__ import annotations

from abc import ABC, abstractmethod

from app.scraping.schemas import ScrapedPromotion, ScrapedSource


class BaseSource(ABC):
    @abstractmethod
    def fetch(self) -> list[ScrapedSource]:
        raise NotImplementedError


class BaseParser(ABC):
    @abstractmethod
    def parse(self, source: ScrapedSource) -> list[ScrapedPromotion]:
        raise NotImplementedError