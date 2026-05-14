"""Tests for DOM-based sale price merge (万 RMB → yuan)."""

from __future__ import annotations

from avto_bot.parser import RawOffer, apply_dom_sale_price_wan


def test_apply_dom_overwrites_none() -> None:
    o = RawOffer(
        offer_id="1",
        title="t",
        series_name=None,
        brand_id=None,
        brand_name=None,
        year=None,
        mileage_km=None,
        price_yuan=None,
        official_price_yuan=None,
        transfer_count=None,
        has_inspection_report=False,
        city_name=None,
        pub_timestamp=None,
        cover_image=None,
        detail_url="https://www.dongchedi.com/usedcar/1",
    )
    n = apply_dom_sale_price_wan([o], {"1": 3.88})
    assert n == 1
    assert o.price_yuan == 38800.0


def test_apply_dom_overwrites_obfuscated_json_value() -> None:
    o = RawOffer(
        offer_id="2",
        title="t",
        series_name=None,
        brand_id=None,
        brand_name=None,
        year=None,
        mileage_km=None,
        price_yuan=999.0,  # wrong / placeholder
        official_price_yuan=None,
        transfer_count=None,
        has_inspection_report=False,
        city_name=None,
        pub_timestamp=None,
        cover_image=None,
        detail_url="https://www.dongchedi.com/usedcar/2",
    )
    n = apply_dom_sale_price_wan([o], {"2": 12.5})
    assert n == 1
    assert o.price_yuan == 125_000.0


def test_apply_dom_skips_when_many_ids_share_one_suspicious_value() -> None:
    """If DOM map collapses to one number for many cards, do not overwrite."""
    offers = [
        RawOffer(
            offer_id=str(i),
            title="t",
            series_name=None,
            brand_id=None,
            brand_name=None,
            year=None,
            mileage_km=None,
            price_yuan=None,
            official_price_yuan=None,
            transfer_count=None,
            has_inspection_report=False,
            city_name=None,
            pub_timestamp=None,
            cover_image=None,
            detail_url=f"https://www.dongchedi.com/usedcar/{i}",
        )
        for i in range(4)
    ]
    id_to_wan = {str(i): 3.0 for i in range(4)}
    n = apply_dom_sale_price_wan(offers, id_to_wan)
    assert n == 0
    assert all(o.price_yuan is None for o in offers)


def test_apply_dom_skips_missing_ids() -> None:
    o = RawOffer(
        offer_id="3",
        title="t",
        series_name=None,
        brand_id=None,
        brand_name=None,
        year=None,
        mileage_km=None,
        price_yuan=None,
        official_price_yuan=None,
        transfer_count=None,
        has_inspection_report=False,
        city_name=None,
        pub_timestamp=None,
        cover_image=None,
        detail_url="https://www.dongchedi.com/usedcar/3",
    )
    n = apply_dom_sale_price_wan([o], {})
    assert n == 0
    assert o.price_yuan is None
