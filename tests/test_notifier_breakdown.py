"""Notifier card: score format and optional breakdown block."""

from __future__ import annotations

import re

from avto_bot.config import ScoringWeights
from avto_bot.notifier import render_card
from avto_bot.parser import RawOffer
from avto_bot.scorer import score_offers


def _one_offer() -> list:
    o = RawOffer(
        offer_id="x",
        title="Test 2022",
        series_name="Test Series",
        brand_id=4,
        brand_name="BMW",
        year=2022,
        mileage_km=30_000,
        price_yuan=250_000,
        official_price_yuan=500_000,
        transfer_count=0,
        has_inspection_report=True,
        city_name="北京",
        pub_timestamp=None,
        cover_image=None,
        detail_url="https://example.com/x",
    )
    peer = RawOffer(
        offer_id="y",
        title="Test 2022b",
        series_name="Test Series",
        brand_id=4,
        brand_name="BMW",
        year=2022,
        mileage_km=35_000,
        price_yuan=350_000,
        official_price_yuan=500_000,
        transfer_count=1,
        has_inspection_report=False,
        city_name="北京",
        pub_timestamp=None,
        cover_image=None,
        detail_url="https://example.com/y",
    )
    weights = ScoringWeights()
    return score_offers([o, peer], weights=weights, new_today_ids={"x", "y"})


def test_render_card_score_two_decimals() -> None:
    scored = _one_offer()
    card = render_card(scored[0], city_code=110000, index=1)
    assert re.search(r"\(\d+\.\d{2}\)", card), "title should contain score like (0.42)"


def test_render_card_breakdown_when_enabled() -> None:
    scored = _one_offer()
    weights = ScoringWeights()
    card = render_card(
        scored[0],
        city_code=110000,
        show_score_breakdown=True,
        scoring_weights=weights,
    )
    assert "Разбор рейтинга" in card
    assert "Свежесть" in card
    assert "Итог" in card
    assert "×" in card or "→" in card


def test_render_card_no_breakdown_when_flag_off() -> None:
    scored = _one_offer()
    card = render_card(scored[0], city_code=110000, show_score_breakdown=False)
    assert "Разбор рейтинга" not in card
