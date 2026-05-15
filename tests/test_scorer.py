"""Smoke tests for the scoring formula and its sub-scores."""

from __future__ import annotations

from datetime import date

import pytest

from avto_bot.config import ScoringWeights
from avto_bot.parser import RawOffer
from avto_bot.scorer import score_offers


def make(
    offer_id: str,
    *,
    price: float | None = 300_000,
    year: int | None = 2022,
    km: float | None = 30_000,
    transfer: int | None = 0,
    inspect: bool = True,
    brand_id: int | None = 4,
    series: str | None = "BMW X5",
) -> RawOffer:
    return RawOffer(
        offer_id=offer_id,
        title=f"{series} {year}",
        series_name=series,
        brand_id=brand_id,
        brand_name="BMW",
        year=year,
        mileage_km=km,
        price_yuan=price,
        official_price_yuan=600_000,
        transfer_count=transfer,
        has_inspection_report=inspect,
        city_name="北京",
        pub_timestamp=None,
        cover_image=None,
        detail_url=f"https://www.dongchedi.com/usedcar/{offer_id}",
    )


def test_higher_score_for_new_today() -> None:
    a = make("a")
    b = make("b")
    weights = ScoringWeights()
    scored = score_offers(
        [a, b],
        weights=weights,
        new_today_ids={"a"},
        yesterday_ids={"b"},
    )
    by_id = {s.offer.offer_id: s for s in scored}
    assert by_id["a"].score > by_id["b"].score
    assert by_id["a"].is_new_today is True
    assert by_id["b"].is_new_today is False


def test_lower_price_wins_within_series() -> None:
    cheap = make("cheap", price=200_000)
    pricey = make("pricey", price=400_000)
    median_peer = make("med", price=300_000)
    weights = ScoringWeights()
    scored = score_offers(
        [cheap, pricey, median_peer],
        weights=weights,
        new_today_ids={"cheap", "pricey", "med"},
    )
    by_id = {s.offer.offer_id: s for s in scored}
    assert by_id["cheap"].breakdown["price_value"] > by_id["pricey"].breakdown["price_value"]
    assert by_id["pricey"].breakdown["price_value"] == 0.0
    assert by_id["cheap"].price_below_median_pct is not None
    assert abs(by_id["cheap"].price_below_median_pct - 100 / 3) < 0.1
    assert by_id["pricey"].price_below_median_pct is None
    assert by_id["med"].price_below_median_pct is None


def test_high_mileage_penalised() -> None:
    fresh = make("low", km=10_000, year=2022)
    worn = make("high", km=200_000, year=2022)
    weights = ScoringWeights()
    scored = score_offers(
        [fresh, worn],
        weights=weights,
        new_today_ids={"low", "high"},
    )
    by_id = {s.offer.offer_id: s for s in scored}
    assert by_id["low"].breakdown["low_km"] > by_id["high"].breakdown["low_km"]
    assert by_id["high"].breakdown["low_km"] == 0.0


def test_owners_scale() -> None:
    weights = ScoringWeights()
    offers = [
        make("o0", transfer=0),
        make("o1", transfer=1),
        make("o2", transfer=2),
        make("o3", transfer=3),
    ]
    scored = {
        s.offer.offer_id: s.breakdown["owners"]
        for s in score_offers(offers, weights=weights, new_today_ids=set())
    }
    assert scored["o0"] == 1.0
    assert scored["o1"] == 0.7
    assert scored["o2"] == 0.4
    assert scored["o3"] == 0.0


def test_score_is_normalised_to_unit_interval() -> None:
    offer = make("perfect", price=100_000, year=date.today().year, km=0, transfer=0)
    weights = ScoringWeights()
    scored = score_offers([offer], weights=weights, new_today_ids={"perfect"})
    assert 0.0 <= scored[0].score <= 1.0


def test_zero_weights_do_not_crash() -> None:
    weights = ScoringWeights(
        freshness=0, price_value=0, low_km=0, owners=0, inspection=0, age=0, premium=0
    )
    scored = score_offers([make("a")], weights=weights, new_today_ids={"a"})
    assert scored[0].score == 0.0
