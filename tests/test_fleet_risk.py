"""Tests for rule-based fleet / commercial-use risk score."""

from __future__ import annotations

from avto_bot.fleet_risk import FLEET_SCORE_MAX, fleet_score
from avto_bot.parsers.dongchedi.parser import RawOffer


def _o(**kw: object) -> RawOffer:
    defaults: dict[str, object] = {
        "offer_id": "1",
        "title": "Test",
        "series_name": "Series",
        "brand_id": 1,
        "brand_name": "B",
        "year": 2020,
        "mileage_km": 50_000.0,
        "price_yuan": 100_000.0,
        "official_price_yuan": None,
        "transfer_count": 0,
        "has_inspection_report": True,
        "city_name": "成都",
        "pub_timestamp": None,
        "cover_image": None,
        "detail_url": "https://www.dongchedi.com/usedcar/1",
    }
    defaults.update(kw)
    return RawOffer(**defaults)  # type: ignore[arg-type]


def test_fleet_high_km_per_year_adds_three() -> None:
    o = _o(year=2020, mileage_km=190_000.0, transfer_count=1, city_name="成都")
    assert fleet_score(o) == 3


def test_single_owner_high_mileage_adds_two() -> None:
    o = _o(
        year=2022,
        mileage_km=90_000.0,
        transfer_count=0,
        city_name="成都",
    )
    assert fleet_score(o) == 2


def test_mega_city_beijing_adds_one() -> None:
    o = _o(city_name="北京", year=2022, mileage_km=10_000.0, transfer_count=0)
    assert fleet_score(o) == 1


def test_taxi_keyword_in_remark_adds_five() -> None:
    snap = {
        "component_list": {
            "document": {
                "car_source_attr": {
                    "merchant_remark": "车况好，曾是出租车",
                }
            }
        }
    }
    o = _o(sophon_snapshot=snap, city_name="成都", year=2022, mileage_km=5_000.0)
    assert fleet_score(o) == 5


def test_taxi_model_substring_adds_two() -> None:
    o = _o(series_name="大众捷达", title="2019款", city_name="成都", year=2019, mileage_km=8_000.0)
    assert fleet_score(o) == 2


def test_fleet_score_max_constant_matches_rules() -> None:
    assert FLEET_SCORE_MAX == 3 + 2 + 1 + 5 + 2
