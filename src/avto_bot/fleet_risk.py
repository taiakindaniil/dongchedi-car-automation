"""Rule-based «fleet / commercial use» risk score (MVP).

Higher score ⇒ more signals that the car may have seen intensive or
commercial use. Does **not** change the main weighted score — only an
explanatory side channel for operators.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from .parsers.dongchedi.parser import RawOffer

CURRENT_YEAR = date.today().year

KM_PER_YEAR_FLEET_THRESHOLD = 30_000.0
SINGLE_OWNER_HIGH_KM = 80_000.0

# Tier-1 cities where fleet / ride-hail density is higher (Chinese names as in API).
FLEET_MEGA_CITIES_CN: frozenset[str] = frozenset(
    {
        "北京",
        "北京市",
        "上海",
        "上海市",
        "深圳",
        "深圳市",
    }
)

# Substrings in series/title — common fleet / former taxi nameplates (not 出租车 text).
TAXI_MODEL_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "捷达",
        "桑塔纳",
        "伊兰特",
        "悦动",
        "威驰",
        "花冠",
        "经典轩逸",
        "轩逸·经典",
        "朗动",
        "福瑞迪",
        "爱丽舍",
        "比亚迪e5",
        "EU5",
        "EU260",
        "绅宝D50",
        "艾瑞泽5",
        "远景",
        "金刚",
    }
)

TAXI_KEYWORD = "出租车"

# Sum of MVP rule weights (UI / Telegram scale).
FLEET_SCORE_MAX = 13


def _is_mega_fleet_city(city: str | None) -> bool:
    if not city or not str(city).strip():
        return False
    raw = str(city).strip()
    if raw in FLEET_MEGA_CITIES_CN:
        return True
    base = raw[:-1] if raw.endswith("市") and len(raw) > 1 else raw
    return base in {"北京", "上海", "深圳"}


def _listing_text_blob(o: RawOffer) -> str:
    parts: list[str] = [o.title or "", o.series_name or ""]
    snap = o.sophon_snapshot
    if isinstance(snap, dict):
        prod = snap.get("product")
        if isinstance(prod, dict):
            pd = prod.get("product_desc")
            if isinstance(pd, dict):
                parts.append(str(pd.get("title") or ""))
                parts.append(str(pd.get("sub_title") or ""))
        cl = snap.get("component_list")
        if isinstance(cl, dict):
            doc = cl.get("document")
            if isinstance(doc, dict):
                csa = doc.get("car_source_attr")
                if isinstance(csa, dict):
                    parts.append(str(csa.get("merchant_remark") or ""))
    return "\n".join(parts)


def _model_haystack(o: RawOffer) -> str:
    return f"{o.series_name or ''}\n{o.title or ''}"


def _age_years_for_fleet(o: RawOffer) -> int | None:
    reg_year: int | None = None
    ts = o.first_register_timestamp
    if ts is not None and ts > 1_000_000_000:
        reg_year = datetime.fromtimestamp(ts, tz=UTC).year
    yr = reg_year if reg_year is not None and 1980 <= reg_year <= 2100 else o.year
    if yr is None:
        return None
    return max(1, CURRENT_YEAR - yr)


def fleet_score(o: RawOffer) -> int:
    """Return a non-negative integer; typical raw range 0–13 for the MVP rules."""
    score = 0

    age_y = _age_years_for_fleet(o)
    if age_y is not None and o.mileage_km is not None and o.mileage_km >= 0:
        km_py = o.mileage_km / age_y
        if km_py > KM_PER_YEAR_FLEET_THRESHOLD:
            score += 3

    owners: int | None = None
    if o.transfer_count is not None:
        owners = o.transfer_count + 1
    if owners == 1 and o.mileage_km is not None and o.mileage_km > SINGLE_OWNER_HIGH_KM:
        score += 2

    if _is_mega_fleet_city(o.city_name):
        score += 1

    blob = _listing_text_blob(o)
    if TAXI_KEYWORD in blob:
        score += 5

    hay = _model_haystack(o)
    if any(tok in hay for tok in TAXI_MODEL_SUBSTRINGS):
        score += 2

    return score
