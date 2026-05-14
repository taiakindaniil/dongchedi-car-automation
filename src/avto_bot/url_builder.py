"""Build dongchedi.com /usedcar/ slug URLs from FiltersConfig.

dongchedi uses a 28-segment positional slug separated by `-`. Empty
positions are `x`. Ranges are `from,to` (one side may be missing → `0,X`
or `X,!1`). Discrete sets allow comma-separated values (e.g. fuels
`4,5,6`).

The mapping below was reverse-engineered from real filter links on the
brand picker page (110000 = Beijing in every sample):

    pos 1: price in 万 RMB         e.g. `0,3` / `15,20` / `20,!1`
    pos 2: body family             0=sedan, 1=SUV, 2=MPV, 4=sport
    pos 4: transmission            1=manual, 2=auto
    pos 5: engine displacement     e.g. `1.1,1.6`
    pos 7: fuel set                1=petrol, 2=diesel, 3=hev, 4=bev,
                                   5=ext_range, 6=phev, 13=mild_hybrid
    pos 9: drive                   1=fwd, 2=rwd, 3=awd
    pos 13: body class             0..5 (sedan), 10..14 (SUV), 20..23 (MPV)
    pos 16: mileage in 万 km        e.g. `0,5`
    pos 17: car age in years       e.g. `1,3` / `10,!1`
    pos 18: emission               2=guo4, 5=guo5, 7=guo6
    pos 19: origin                 1=jv, 2=domestic, 3=jv_domestic, 4=import
    pos 20: brand id
    pos 22: city GB code           e.g. 110000 = Beijing
    pos 23: page number            1, 2, ...
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from .config import (
    BodyClass,
    BodyFamily,
    Drive,
    Emission,
    FiltersConfig,
    Fuel,
    Origin,
    Transmission,
)

BASE_URL: Final[str] = "https://www.dongchedi.com/usedcar/"
SLOT_COUNT: Final[int] = 28

# 0-indexed positions inside the slug.
POS_PRICE: Final[int] = 0
POS_BODY_FAMILY: Final[int] = 1
POS_TRANSMISSION: Final[int] = 3
POS_DISPLACEMENT: Final[int] = 4
POS_FUEL: Final[int] = 6
POS_DRIVE: Final[int] = 8
POS_BODY_CLASS: Final[int] = 12
POS_MILEAGE: Final[int] = 15
POS_AGE: Final[int] = 16
POS_EMISSION: Final[int] = 17
POS_ORIGIN: Final[int] = 18
POS_BRAND: Final[int] = 19
POS_CITY: Final[int] = 21
POS_PAGE: Final[int] = 22


_BODY_FAMILY_CODE: dict[BodyFamily, str] = {
    BodyFamily.sedan: "0",
    BodyFamily.suv: "1",
    BodyFamily.mpv: "2",
    BodyFamily.sport: "4",
}

_BODY_CLASS_CODE: dict[BodyClass, str] = {
    BodyClass.micro: "0",
    BodyClass.small: "1",
    BodyClass.compact: "2",
    BodyClass.mid: "3",
    BodyClass.mid_large: "4",
    BodyClass.large: "5",
    BodyClass.small_suv: "10",
    BodyClass.compact_suv: "11",
    BodyClass.mid_suv: "12",
    BodyClass.mid_large_suv: "13",
    BodyClass.large_suv: "14",
    BodyClass.small_mpv: "20",
    BodyClass.compact_mpv: "21",
    BodyClass.mid_mpv: "22",
    BodyClass.large_mpv: "23",
}

_FUEL_CODE: dict[Fuel, str] = {
    Fuel.petrol: "1",
    Fuel.diesel: "2",
    Fuel.hev: "3",
    Fuel.bev: "4",
    Fuel.ext_range: "5",
    Fuel.phev: "6",
    Fuel.mild_hybrid: "13",
}

_TRANSMISSION_CODE: dict[Transmission, str] = {
    Transmission.manual: "1",
    Transmission.auto: "2",
}

_DRIVE_CODE: dict[Drive, str] = {
    Drive.fwd: "1",
    Drive.rwd: "2",
    Drive.awd: "3",
}

_EMISSION_CODE: dict[Emission, str] = {
    Emission.guo4: "2",
    Emission.guo5: "5",
    Emission.guo6: "7",
}

_ORIGIN_CODE: dict[Origin, str] = {
    Origin.jv: "1",
    Origin.domestic: "2",
    Origin.jv_domestic: "3",
    Origin.import_: "4",
}


def _range_slot(lo: float | int | None, hi: float | int | None) -> str:
    """Encode a numeric range as the slug expects.

    Conventions on the site:
      * fully open  → `x`
      * lower-open  → `0,hi`
      * upper-open  → `lo,!1`
      * both-bound  → `lo,hi`
    """
    if lo is None and hi is None:
        return "x"
    lo_s = _num(lo) if lo is not None else "0"
    hi_s = _num(hi) if hi is not None else "!1"
    return f"{lo_s},{hi_s}"


def _num(v: float | int) -> str:
    """Stringify a number, dropping the trailing `.0` for ints-like floats."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _csv_codes(values: list, mapping: dict) -> str:
    if not values:
        return "x"
    codes = [mapping[v] for v in values if v in mapping]
    return ",".join(codes) if codes else "x"


def build_url(
    filters: FiltersConfig,
    *,
    page: int = 1,
    brand_id: int | None = None,
    body_family: BodyFamily | None = None,
    body_class: BodyClass | None = None,
) -> str:
    """Compose one /usedcar/ URL.

    The caller may override `brand_id`, `body_family`, `body_class` to
    fan out a multi-value filter into several single-value requests (the
    site is more forgiving with single-value slugs and we don't have to
    guess whether it supports multi-brand selection in the URL).
    """
    slots = ["x"] * SLOT_COUNT

    slots[POS_PRICE] = _range_slot(*filters.price_wan)

    # Body family: explicit override > single-value list > none.
    fam = body_family
    if fam is None and len(filters.body_family) == 1:
        fam = filters.body_family[0]
    if fam is not None:
        slots[POS_BODY_FAMILY] = _BODY_FAMILY_CODE[fam]

    if filters.transmission is not None:
        slots[POS_TRANSMISSION] = _TRANSMISSION_CODE[filters.transmission]

    if filters.fuel:
        slots[POS_FUEL] = _csv_codes(filters.fuel, _FUEL_CODE)

    if filters.drive is not None:
        slots[POS_DRIVE] = _DRIVE_CODE[filters.drive]

    cls = body_class
    if cls is None and len(filters.body_class) == 1:
        cls = filters.body_class[0]
    if cls is not None:
        slots[POS_BODY_CLASS] = _BODY_CLASS_CODE[cls]

    if filters.km_max_wan is not None:
        slots[POS_MILEAGE] = _range_slot(0, filters.km_max_wan)

    slots[POS_AGE] = _range_slot(*filters.year_range_to_age_range())

    if filters.emission is not None:
        slots[POS_EMISSION] = _EMISSION_CODE[filters.emission]

    if filters.origin is not None:
        slots[POS_ORIGIN] = _ORIGIN_CODE[filters.origin]

    bid = brand_id
    if bid is None and len(filters.brand_ids) == 1:
        bid = filters.brand_ids[0]
    if bid is not None:
        slots[POS_BRAND] = str(bid)

    if filters.city is not None:
        slots[POS_CITY] = str(filters.city)

    slots[POS_PAGE] = str(max(1, page))

    return BASE_URL + "-".join(slots)


def iter_urls(filters: FiltersConfig) -> Iterator[tuple[int, str]]:
    """Yield (page, url) for every brand × family × class combination.

    The Cartesian product is intentionally bounded by `pages_to_scan` on
    one axis and by user-chosen multi-value lists on the other, so the
    operator controls the request budget via YAML.
    """
    brand_axis: list[int | None] = list(filters.brand_ids) if filters.brand_ids else [None]
    family_axis: list[BodyFamily | None] = (
        list(filters.body_family) if len(filters.body_family) > 1 else [None]
    )
    class_axis: list[BodyClass | None] = (
        list(filters.body_class) if len(filters.body_class) > 1 else [None]
    )

    for bid in brand_axis:
        for fam in family_axis:
            for cls in class_axis:
                for page in range(1, filters.pages_to_scan + 1):
                    yield page, build_url(
                        filters,
                        page=page,
                        brand_id=bid,
                        body_family=fam,
                        body_class=cls,
                    )
