"""Verify URL slugs against ground-truth links scraped from dongchedi.com."""

from __future__ import annotations

from avto_bot.config import (
    BodyClass,
    BodyFamily,
    Drive,
    Emission,
    FiltersConfig,
    Fuel,
    Origin,
    Transmission,
    UsedCarListSort,
)
from avto_bot.parsers.dongchedi.url_builder import build_url, iter_urls

BASE = "https://www.dongchedi.com/usedcar/"


def test_empty_filters_match_default_page1() -> None:
    f = FiltersConfig(city=110000)
    # Aligns with dongchedi list URL …-110000-1-4-… (newly published first).
    assert build_url(f) == BASE + "x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_list_sort_site_default_restores_trailing_x_slot() -> None:
    f = FiltersConfig(city=110000, list_sort=UsedCarListSort.site_default)
    assert build_url(f) == BASE + "x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-x-x-x-x-x"


def test_brand_only_matches_audi_link() -> None:
    # 奥迪 link from the page: brand_id=2, city=110000, page=1
    f = FiltersConfig(city=110000, brand_ids=[2])
    assert build_url(f) == BASE + "x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-2-x-110000-1-4-x-x-x-x"


def test_sedan_family() -> None:
    f = FiltersConfig(city=110000, body_family=[BodyFamily.sedan])
    assert build_url(f) == BASE + "x-0-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_suv_family() -> None:
    f = FiltersConfig(city=110000, body_family=[BodyFamily.suv])
    assert build_url(f) == BASE + "x-1-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_compact_suv_class() -> None:
    f = FiltersConfig(city=110000, body_class=[BodyClass.compact_suv])
    assert build_url(f) == BASE + "x-x-x-x-x-x-x-x-x-x-x-x-11-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_price_under_3wan() -> None:
    f = FiltersConfig(city=110000, price_wan=(None, 3))
    assert build_url(f) == BASE + "0,3-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_price_over_20wan() -> None:
    f = FiltersConfig(city=110000, price_wan=(20, None))
    assert build_url(f) == BASE + "20,!1-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_price_range_15_20wan() -> None:
    f = FiltersConfig(city=110000, price_wan=(15, 20))
    assert build_url(f) == BASE + "15,20-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_mileage_under_5wan() -> None:
    f = FiltersConfig(city=110000, km_max_wan=5)
    assert build_url(f) == BASE + "x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-0,5-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_fuel_bev() -> None:
    f = FiltersConfig(city=110000, fuel=[Fuel.bev])
    assert build_url(f) == BASE + "x-x-x-x-x-x-4-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_fuel_new_energy_combo() -> None:
    # 新能源 = pure BEV + range-extended + PHEV
    f = FiltersConfig(city=110000, fuel=[Fuel.bev, Fuel.ext_range, Fuel.phev])
    assert build_url(f) == BASE + "x-x-x-x-x-x-4,5,6-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_manual_transmission() -> None:
    f = FiltersConfig(city=110000, transmission=Transmission.manual)
    assert build_url(f) == BASE + "x-x-x-1-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_awd_drive() -> None:
    f = FiltersConfig(city=110000, drive=Drive.awd)
    assert build_url(f) == BASE + "x-x-x-x-x-x-x-x-3-x-x-x-x-x-x-x-x-x-x-x-x-110000-1-4-x-x-x-x"


def test_emission_guo6() -> None:
    f = FiltersConfig(city=110000, emission=Emission.guo6)
    assert build_url(f) == BASE + "x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-7-x-x-x-110000-1-4-x-x-x-x"


def test_origin_import() -> None:
    f = FiltersConfig(city=110000, origin=Origin.import_)
    assert build_url(f) == BASE + "x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-4-x-x-110000-1-4-x-x-x-x"


def test_year_range_translates_to_age() -> None:
    f = FiltersConfig(city=110000, year_range=(2020, 2025))
    age_from, age_to = f.year_range_to_age_range(now_year=2026)
    assert (age_from, age_to) == (1, 6)


def test_pagination_increments_page_slot() -> None:
    f = FiltersConfig(city=110000)
    p2 = build_url(f, page=2)
    assert p2 == BASE + "x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-110000-2-4-x-x-x-x"


def test_iter_urls_fanout_per_brand() -> None:
    f = FiltersConfig(
        city=110000,
        brand_ids=[3, 4],   # Mercedes + BMW
        pages_to_scan=2,
    )
    urls = list(iter_urls(f))
    assert len(urls) == 4  # 2 brands × 2 pages
    pages = {p for p, _ in urls}
    assert pages == {1, 2}
    # First two URLs should be brand 3 (Mercedes), pages 1..2
    assert "-3-x-110000-1-4-" in urls[0][1]
    assert "-3-x-110000-2-4-" in urls[1][1]
    assert "-4-x-110000-1-4-" in urls[2][1]


def test_slot_count_invariant() -> None:
    f = FiltersConfig(
        city=110000,
        brand_ids=[4],
        body_family=[BodyFamily.suv],
        body_class=[BodyClass.mid_suv],
        price_wan=(10, 40),
        km_max_wan=10,
        fuel=[Fuel.petrol, Fuel.phev],
        transmission=Transmission.auto,
        drive=Drive.awd,
        emission=Emission.guo6,
        origin=Origin.import_,
    )
    url = build_url(f, page=3)
    slug = url[len(BASE):]
    assert slug.count("-") == 27  # 28 segments
