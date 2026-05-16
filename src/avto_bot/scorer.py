"""Weighted scoring formula for ranking offers within a single scan.

We compute seven independent sub-scores, each normalised into `[0, 1]`,
then combine them with the operator's weights from YAML. The combined
score is also normalised by the sum of weights so the YAML doesn't have
to add up to 1.0 exactly — operators can dial knobs without doing
arithmetic in their head.

Why a market-relative price score?

  We don't have a ground-truth price index. But within a single scan we
  always have N peer listings of the same `series_name`, which gives us
  a serviceable local median. A car at 30% below the median for its
  series-and-page is almost always a real deal (or has hidden issues
  that a human will catch in 30 seconds on the detail page).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from .brands import PREMIUM_BRAND_IDS
from .config import ScoringWeights
from .parsers.dongchedi.parser import RawOffer

CURRENT_YEAR_FOR_AGE = date.today().year
KM_PER_YEAR_BUDGET = 20_000.0  # above this we treat the car as "tired"
AGE_HORIZON_YEARS = 10.0


@dataclass(slots=True)
class ScoredOffer:
    offer: RawOffer
    score: float
    breakdown: dict[str, float]
    is_new_today: bool
    # Positive when ``price_yuan`` is strictly below the series (or global) median.
    price_below_median_pct: float | None = None


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _freshness(offer_id: str, new_today_ids: set[str], yesterday_ids: set[str]) -> float:
    if offer_id in new_today_ids:
        return 1.0
    if offer_id in yesterday_ids:
        return 0.5
    return 0.0


def _build_series_medians(offers: list[RawOffer]) -> dict[str, float]:
    """Group prices by series name (fallback to title) and take medians.

    Singletons fall back to the global median so a one-off model still
    gets a sensible value score (vs. zero peers ⇒ NaN ⇒ skip).
    """
    by_series: dict[str, list[float]] = defaultdict(list)
    all_prices: list[float] = []
    for o in offers:
        if o.price_yuan is None or o.price_yuan <= 0:
            continue
        key = o.series_name or o.title or "_unknown"
        by_series[key].append(o.price_yuan)
        all_prices.append(o.price_yuan)
    global_median = statistics.median(all_prices) if all_prices else 0.0
    return {
        key: (statistics.median(prices) if len(prices) >= 2 else global_median)
        for key, prices in by_series.items()
    }


def _price_value(offer: RawOffer, medians: dict[str, float]) -> float:
    if offer.price_yuan is None or offer.price_yuan <= 0:
        return 0.0
    key = offer.series_name or offer.title or "_unknown"
    median = medians.get(key, 0.0)
    if median <= 0:
        return 0.0
    delta = (median - offer.price_yuan) / median
    # Cap at 50% discount — anything bigger is more likely bad data than a steal.
    return _clip01(delta / 0.5)


def _low_km(offer: RawOffer) -> float:
    if offer.mileage_km is None or offer.year is None or offer.mileage_km < 0:
        return 0.5  # unknown — neutral
    age = max(1, CURRENT_YEAR_FOR_AGE - offer.year)
    km_per_year = offer.mileage_km / age
    return _clip01(1.0 - km_per_year / KM_PER_YEAR_BUDGET)


def _owners(offer: RawOffer) -> float:
    n = offer.transfer_count
    if n is None:
        return 0.5
    if n <= 0:
        return 1.0
    if n == 1:
        return 0.7
    if n == 2:
        return 0.4
    return 0.0


def _inspection(offer: RawOffer) -> float:
    return 1.0 if offer.has_inspection_report else 0.0


def _age(offer: RawOffer) -> float:
    if offer.year is None:
        return 0.5
    age = max(0, CURRENT_YEAR_FOR_AGE - offer.year)
    return _clip01(1.0 - age / AGE_HORIZON_YEARS)


def _premium(offer: RawOffer) -> float:
    if offer.brand_id is None:
        return 0.0
    return 1.0 if offer.brand_id in PREMIUM_BRAND_IDS else 0.0


def score_offers(
    offers: list[RawOffer],
    *,
    weights: ScoringWeights,
    new_today_ids: set[str],
    yesterday_ids: set[str] | None = None,
) -> list[ScoredOffer]:
    """Score every offer in `offers` and return the list ordered by score.

    The score sum is normalised by the sum of weights so YAML weights
    can be arbitrary positive numbers — they don't have to total 1.0.
    """
    yesterday_ids = yesterday_ids or set()
    medians = _build_series_medians(offers)
    weight_total = weights.total() or 1.0

    out: list[ScoredOffer] = []
    for o in offers:
        parts = {
            "freshness":   _freshness(o.offer_id, new_today_ids, yesterday_ids),
            "price_value": _price_value(o, medians),
            "low_km":      _low_km(o),
            "owners":      _owners(o),
            "inspection":  _inspection(o),
            "age":         _age(o),
            "premium":     _premium(o),
        }
        weighted = (
            weights.freshness   * parts["freshness"]
            + weights.price_value * parts["price_value"]
            + weights.low_km      * parts["low_km"]
            + weights.owners      * parts["owners"]
            + weights.inspection  * parts["inspection"]
            + weights.age         * parts["age"]
            + weights.premium     * parts["premium"]
        )
        score = weighted / weight_total
        key = o.series_name or o.title or "_unknown"
        median_price = medians.get(key, 0.0)
        pct_below: float | None = None
        if (
            o.price_yuan is not None
            and o.price_yuan > 0
            and median_price > 0
            and o.price_yuan < median_price
        ):
            pct_below = (median_price - o.price_yuan) / median_price * 100.0
        out.append(
            ScoredOffer(
                offer=o,
                score=score,
                breakdown=parts,
                is_new_today=o.offer_id in new_today_ids,
                price_below_median_pct=pct_below,
            )
        )
    out.sort(key=lambda s: s.score, reverse=True)
    return out
