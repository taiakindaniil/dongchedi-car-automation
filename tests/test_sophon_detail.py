"""Sophon mobile card/detail JSON → RawOffer enrichment."""

from __future__ import annotations

import json
from pathlib import Path

from avto_bot.parsers.dongchedi.parser import RawOffer
from avto_bot.parsers.dongchedi.sophon_detail import apply_sophon_detail_to_offer


def _base_offer(*, price_yuan: float | None = None) -> RawOffer:
    return RawOffer(
        offer_id="23049567",
        title="启辰星",
        series_name="启辰星",
        brand_id=109,
        brand_name="启辰",
        year=2023,
        mileage_km=33_200.0,
        price_yuan=price_yuan,
        official_price_yuan=None,
        transfer_count=0,
        has_inspection_report=True,
        city_name="北京",
        pub_timestamp=None,
        cover_image=None,
        detail_url="https://www.dongchedi.com/usedcar/23049567",
    )


def test_apply_sophon_detail_from_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sophon_card_detail.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    o = _base_offer(price_yuan=None)
    assert apply_sophon_detail_to_offer(o, body) is True
    assert o.first_register_timestamp == 1682870400
    assert o.year == 2023
    assert abs(o.market_valuation_yuan - 69_300.0) < 0.01
    assert abs(o.price_yuan - 63_800.0) < 0.01
    assert o.is_accident is False
    assert o.is_soaked is False
    assert o.is_burned is False
    assert o.is_changed_mileage is False
    snap = o.sophon_snapshot
    assert snap is not None
    cl = snap["component_list"]
    assert "document" in cl
    assert cl["document"]["car_base_attr"]["gear_box"] == "自动"
    assert cl["price_analysis"]["price_level"] == 2
    assert (
        cl["maintenance"]["report_info"]["inspection_report_id"]
        == "R7618896092151500824"
    )
    assert "global_info" not in snap
    assert "bottom_bar" not in cl
    imgs = snap["product"]["product_image"]["image_list"]
    assert len(imgs) == 5
    assert imgs[0]["url"] == "https://example.com/1.webp"


def test_sophon_snapshot_truncates_merchant_remark() -> None:
    from avto_bot.parsers.dongchedi import sophon_detail as sd

    fixture = Path(__file__).parent / "fixtures" / "sophon_card_detail.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    long_txt = "ы" * 5000
    body["data"]["component_list"]["document"]["car_source_attr"]["merchant_remark"] = (
        long_txt
    )
    o = _base_offer()
    assert apply_sophon_detail_to_offer(o, body) is True
    mr = o.sophon_snapshot["component_list"]["document"]["car_source_attr"][
        "merchant_remark"
    ]
    assert len(mr) <= sd.MERCHANT_REMARK_MAX + 30
    assert "…" in mr


def test_sophon_snapshot_not_set_on_reject() -> None:
    o = _base_offer()
    assert apply_sophon_detail_to_offer(o, {"status": 1, "data": {}}) is False
    assert o.sophon_snapshot is None


def test_apply_sophon_detail_rejects_bad_status() -> None:
    o = _base_offer(price_yuan=100_000)
    assert apply_sophon_detail_to_offer(o, {"status": 1, "data": {}}) is False


def test_apply_sophon_detail_sets_accident_flag() -> None:
    body = json.loads(
        (Path(__file__).parent / "fixtures" / "sophon_card_detail.json").read_text(
            encoding="utf-8"
        )
    )
    body["data"]["component_list"]["maintenance"]["report_info"]["conclusion"][
        "is_accident"
    ] = 1
    o = _base_offer()
    apply_sophon_detail_to_offer(o, body)
    assert o.is_accident is True
