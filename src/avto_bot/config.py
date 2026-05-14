"""Loads `.env` settings via pydantic-settings and user filters from YAML.

The split is intentional: secrets / infra (BOT_TOKEN, proxy, paths, log level)
live in environment variables; product knobs (filters, weights, schedule) live
in a hand-editable YAML so the user can iterate without restarting via env.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BodyFamily(str, Enum):
    sedan = "sedan"
    suv = "suv"
    mpv = "mpv"
    sport = "sport"


class BodyClass(str, Enum):
    # Sedan classes (URL position 13: 0..5)
    micro = "micro"          # 微型车
    small = "small"          # 小型车
    compact = "compact"      # 紧凑型车
    mid = "mid"              # 中型车
    mid_large = "mid_large"  # 中大型车
    large = "large"          # 大型车

    # SUV classes (URL position 13: 10..14)
    small_suv = "small_suv"          # 小型SUV
    compact_suv = "compact_suv"      # 紧凑型SUV
    mid_suv = "mid_suv"              # 中型SUV
    mid_large_suv = "mid_large_suv"  # 中大型SUV
    large_suv = "large_suv"          # 大型SUV

    # MPV classes (URL position 13: 20..23)
    small_mpv = "small_mpv"
    compact_mpv = "compact_mpv"
    mid_mpv = "mid_mpv"
    large_mpv = "large_mpv"


class Fuel(str, Enum):
    petrol = "petrol"
    diesel = "diesel"
    hev = "hev"
    bev = "bev"
    ext_range = "ext_range"
    phev = "phev"
    mild_hybrid = "mild_hybrid"


class Transmission(str, Enum):
    manual = "manual"
    auto = "auto"


class Drive(str, Enum):
    fwd = "fwd"
    rwd = "rwd"
    awd = "awd"


class Emission(str, Enum):
    guo4 = "guo4"
    guo5 = "guo5"
    guo6 = "guo6"


class Origin(str, Enum):
    jv = "jv"
    domestic = "domestic"
    jv_domestic = "jv_domestic"
    import_ = "import"


class FiltersConfig(BaseModel):
    city: int | None = None
    brand_ids: list[int] = Field(default_factory=list)
    body_family: list[BodyFamily] = Field(default_factory=list)
    body_class: list[BodyClass] = Field(default_factory=list)
    price_wan: tuple[float | None, float | None] = (None, None)
    year_range: tuple[int | None, int | None] = (None, None)
    km_max_wan: float | None = None
    fuel: list[Fuel] = Field(default_factory=list)
    transmission: Transmission | None = None
    drive: Drive | None = None
    emission: Emission | None = None
    origin: Origin | None = None
    inspected_only: bool = False
    pages_to_scan: int = Field(default=3, ge=1, le=20)

    @model_validator(mode="before")
    @classmethod
    def coerce_ranges(cls, v: dict) -> dict:
        for key in ("price_wan", "year_range"):
            if key in v and isinstance(v[key], list):
                while len(v[key]) < 2:
                    v[key].append(None)
                v[key] = tuple(v[key][:2])
        return v

    def year_range_to_age_range(
        self, now_year: int | None = None
    ) -> tuple[int | None, int | None]:
        """Convert a model-year range to the (age_from, age_to) the slug expects.

        The site filters by car age in years, not by manufacturing year.
        Newer cars have lower age, so the bounds flip: a `year_range` of
        `(2020, 2025)` with `now_year=2026` becomes `(1, 6)`.
        """
        year_from, year_to = self.year_range
        if year_from is None and year_to is None:
            return None, None
        now = now_year or date.today().year
        age_from = max(0, now - year_to) if year_to is not None else None
        age_to = max(0, now - year_from) if year_from is not None else None
        if age_from is not None and age_to is not None and age_from > age_to:
            age_from, age_to = age_to, age_from
        return age_from, age_to


class ScoringWeights(BaseModel):
    freshness: float = 0.30
    price_value: float = 0.20
    low_km: float = 0.15
    owners: float = 0.10
    inspection: float = 0.10
    age: float = 0.10
    premium: float = 0.05

    def total(self) -> float:
        return (
            self.freshness
            + self.price_value
            + self.low_km
            + self.owners
            + self.inspection
            + self.age
            + self.premium
        )


class NotifyConfig(BaseModel):
    top_n_per_day: int = Field(default=10, ge=1, le=100)
    schedule_cron: str = "0 10 * * *"
    min_score: float = Field(default=0.40, ge=0.0, le=1.0)
    send_photos: bool = True


class AppConfig(BaseModel):
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    scoring_weights: ScoringWeights = Field(default_factory=ScoringWeights)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = ""
    chat_id: str = ""
    http_proxy: str | None = None
    headless: bool = True
    timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"
    config_path: Path = Path("config/filters.yaml")
    db_path: Path = Path("data/seen.db")
    # Playwright Chromium user-data-dir: cookies, localStorage, IndexedDB
    # persist here so repeat runs reuse the same session (msToken / a_bogus flow).
    playwright_user_data_dir: Path = Path("data/playwright_profile")
    # JSON cache of PUA→digit maps built from intercepted .woff2 (keyed by sha256 of font bytes).
    font_map_cache_dir: Path = Path("data/font_map_cache")


def load_app_config(path: Path) -> AppConfig:
    """Parse the YAML at `path` and validate it as `AppConfig`.

    Missing sections fall back to defaults, so an empty file still produces a
    valid (but uselessly broad) config rather than crashing the service.
    """
    if not path.exists():
        return AppConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
