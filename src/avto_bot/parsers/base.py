"""Abstract listing URL builders: one implementation per marketplace parser."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from avto_bot.config import BodyClass, BodyFamily, FiltersConfig


class ListingUrlBuilder(ABC):
    """Builds search/listing URLs from shared ``FiltersConfig``.

    Each concrete parser (dongchedi, …) maps the same filter model to its
    site's URL conventions.
    """

    @abstractmethod
    def build_url(
        self,
        filters: FiltersConfig,
        *,
        page: int = 1,
        brand_id: int | None = None,
        body_family: BodyFamily | None = None,
        body_class: BodyClass | None = None,
    ) -> str:
        """Compose one listing URL for ``page`` and optional single-value overrides."""

    @abstractmethod
    def iter_urls(self, filters: FiltersConfig) -> Iterator[tuple[int, str]]:
        """Yield ``(page, url)`` for every fan-out combination the builder supports."""
