"""End-to-end smoke test that wires every component except the live Playwright.

The Playwright path is unit-covered indirectly via the JSON parsing
helpers; this test verifies the rest of the pipeline (URL building,
storage upsert + dedup, scoring, card rendering) using synthetic
`RawOffer`s. That way CI doesn't depend on dongchedi.com being reachable.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from avto_bot.config import AppConfig, FiltersConfig, NotifyConfig, ScoringWeights
from avto_bot.integrations.notifications.telegram import (
    render_card,
    render_digest_header,
)
from avto_bot.parsers.dongchedi.parser import RawOffer
from avto_bot.parsers.dongchedi.url_builder import iter_urls
from avto_bot.scorer import score_offers
from avto_bot.storage import Storage


def make(
    oid: str,
    *,
    price: float = 300_000,
    year: int = 2022,
    km: float = 30_000,
    transfer: int = 0,
    inspect: bool = True,
    brand_id: int = 4,
    series: str = "宝马X5",
) -> RawOffer:
    return RawOffer(
        offer_id=oid,
        title=f"{series} {year}款",
        series_name=series,
        brand_id=brand_id,
        brand_name="宝马",
        year=year,
        mileage_km=km,
        price_yuan=price,
        official_price_yuan=600_000,
        transfer_count=transfer,
        has_inspection_report=inspect,
        city_name="北京",
        pub_timestamp=None,
        cover_image=None,
        detail_url=f"https://www.dongchedi.com/usedcar/{oid}",
    )


@pytest.mark.asyncio
async def test_pipeline_end_to_end(tmp_path: Path) -> None:
    cfg = AppConfig(
        filters=FiltersConfig(
            city=110000,
            brand_ids=[4, 3],   # BMW + Mercedes
            pages_to_scan=2,
        ),
        scoring_weights=ScoringWeights(),
        notify=NotifyConfig(top_n_per_day=5, min_score=0.0),
    )

    # 1. URL fanout: 2 brands × 2 pages = 4 URLs, no duplicates.
    urls = list(iter_urls(cfg.filters))
    assert len(urls) == 4
    assert len({u for _, u in urls}) == 4

    # 2. Storage: first run = everything is new; second run = nothing.
    storage = Storage(tmp_path / "smoke.db")
    await storage.connect()
    try:
        offers = [
            make("a", price=200_000, transfer=0, year=2023),
            make("b", price=300_000, transfer=1, year=2021),
            make("c", price=400_000, transfer=2, year=2019),
        ]
        new_first = await storage.upsert_offers(offers)
        assert new_first == {"a", "b", "c"}

        new_second = await storage.upsert_offers(offers)
        # Within the same day the set stays the same — see Storage.upsert_offers
        assert new_second == {"a", "b", "c"}

        pending = await storage.pending_for_notification()
        assert set(pending) == {"a", "b", "c"}

        # 3. Scoring picks "a" (cheap, no transfers, recent) on top.
        scored = score_offers(
            offers,
            weights=cfg.scoring_weights,
            new_today_ids=new_first,
        )
        assert scored[0].offer.offer_id == "a"

        for s in scored:
            await storage.set_score(s.offer.offer_id, s.score)

        # 4. Card rendering uses HTML and includes the deep link.
        card = render_card(scored[0], city_code=cfg.filters.city)
        assert "宝马" in card or "宝马X5" in card
        assert "https://www.dongchedi.com/usedcar/a" in card
        assert "5778550614669660455" in card  # mileage tg-emoji
        assert "Пробег:" in card
        assert "Пекин" in card
        assert "(0." in card  # score in title
        assert "5312241539987020022" in card  # below-median fire tg-emoji
        assert "ниже медианы" in card

        header = render_digest_header(
            today=date.today(), new_count=3, sending_count=3, scanned=3
        )
        assert "Авто-дайджест" in header

        # 5. After marking notified, pending shrinks.
        await storage.mark_notified(["a", "b"])
        pending_after = set(await storage.pending_for_notification())
        assert pending_after == {"c"}
    finally:
        await storage.close()
