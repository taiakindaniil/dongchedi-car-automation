"""dongchedi.com ``/usedcar/`` slug URLs from ``FiltersConfig``.

Implements :class:`ListingUrlBuilder` for the 28-segment positional slug.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from ...config import (
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
from ..base import ListingUrlBuilder

BASE_URL: Final[str] = "https://www.dongchedi.com/usedcar/"
SLOT_COUNT: Final[int] = 28

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
POS_SORT: Final[int] = 23
# Second-to-last slug segment (index 26 of 28): ``1`` = only listings with 检测报告.
POS_INSPECTED: Final[int] = 26

_LIST_SORT_TO_SLUG: Final[dict[UsedCarListSort, str]] = {
    UsedCarListSort.site_default: "x",
    UsedCarListSort.newly_published_first: "4",
}

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
    if lo is None and hi is None:
        return "x"
    lo_s = _num(lo) if lo is not None else "0"
    hi_s = _num(hi) if hi is not None else "!1"
    return f"{lo_s},{hi_s}"


def _num(v: float | int) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _csv_codes(values: list, mapping: dict) -> str:
    if not values:
        return "x"
    codes = [mapping[v] for v in values if v in mapping]
    return ",".join(codes) if codes else "x"


class DongchediListingUrlBuilder(ListingUrlBuilder):
    """28-segment ``-`` slug used on dongchedi used-car search pages."""

    def build_url(
        self,
        filters: FiltersConfig,
        *,
        page: int = 1,
        brand_id: int | None = None,
        body_family: BodyFamily | None = None,
        body_class: BodyClass | None = None,
    ) -> str:
        slots = ["x"] * SLOT_COUNT

        slots[POS_PRICE] = _range_slot(*filters.price_wan)

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

        slots[POS_SORT] = _LIST_SORT_TO_SLUG[filters.list_sort]

        if filters.inspected_only:
            slots[POS_INSPECTED] = "1"

        return BASE_URL + "-".join(slots)

    def iter_urls(self, filters: FiltersConfig) -> Iterator[tuple[int, str]]:
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
                        yield page, self.build_url(
                            filters,
                            page=page,
                            brand_id=bid,
                            body_family=fam,
                            body_class=cls,
                        )


default_dongchedi_url_builder = DongchediListingUrlBuilder()

build_url = default_dongchedi_url_builder.build_url
iter_urls = default_dongchedi_url_builder.iter_urls
