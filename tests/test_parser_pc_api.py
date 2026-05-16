"""Tests for PC API payload shape (search_sh_sku_info_list)."""

from __future__ import annotations

import pytest

from avto_bot.parsers.dongchedi.parser import (
    _normalise_offer,
    extract_offers_from_api_payload,
)


def test_normalise_pc_row_inspection_from_tags() -> None:
    raw = {
        "sku_id": 23579640,
        "title": "唐DM 2015款 DM 2.0T 四驱旗舰型",
        "series_name": "唐DM",
        "brand_id": 16,
        "brand_name": "比亚迪",
        "car_year": 2015,
        "transfer_cnt": 0,
        "car_source_city_name": "深圳",
        "image": "https://example.com/img.jpg",
        "sh_price": ".",
        "tags": [{"text": "检测报告", "text_color": "rgba(179,125,18,1)"}],
    }
    o = _normalise_offer(raw)
    assert o is not None
    assert o.offer_id == "23579640"
    assert o.year == 2015
    assert o.transfer_count == 0
    assert o.has_inspection_report is True
    assert o.city_name == "深圳"
    assert o.cover_image == "https://example.com/img.jpg"
    assert o.price_yuan is None  # obfuscated string


def test_normalise_mileage_from_sub_title_plaintext() -> None:
    raw = {
        "sku_id": 1,
        "title": "Car",
        "sub_title": "2020款 豪华版 | 5.2万公里",
    }
    o = _normalise_offer(raw)
    assert o is not None
    assert o.mileage_km == pytest.approx(52_000.0)


def test_extract_search_sh_sku_info_list() -> None:
    payload = {
        "data": {
            "has_more": True,
            "search_sh_sku_info_list": [
                {"sku_id": 1, "title": "A", "series_name": "S", "brand_id": 4},
                {"sku_id": 2, "title": "B", "series_name": "S2", "brand_id": 3},
            ],
            "total": 10000,
        },
        "status": 0,
    }
    offers = extract_offers_from_api_payload(payload)
    assert len(offers) == 2
    assert {o.offer_id for o in offers} == {"1", "2"}
