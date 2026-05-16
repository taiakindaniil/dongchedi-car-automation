"""Map mobile Sophon ``card/detail`` JSON onto `RawOffer` fields."""

from __future__ import annotations

import copy
import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

MERCHANT_REMARK_MAX = 4000
MAX_SNAPSHOT_UTF8_BYTES = 384_000
IMAGE_LIST_CAP = 5


def _fen_to_yuan(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n / 100.0


def _bool_conclusion(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(int(v))
    return None


def _snapshot_utf8_size(snap: dict[str, Any]) -> int:
    return len(json.dumps(snap, ensure_ascii=False).encode("utf-8"))


def _truncate_merchant_remark(d: dict[str, Any], limit: int) -> None:
    mr = d.get("merchant_remark")
    if isinstance(mr, str) and len(mr) > limit:
        d["merchant_remark"] = mr[:limit] + "\n…[truncated]"


def _strip_image_item(it: dict[str, Any]) -> dict[str, Any]:
    keys = ("url", "small_url", "type", "image_position", "uri")
    return {k: it[k] for k in keys if k in it}


def _prune_product_images(pi: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    mi = pi.get("main_image")
    if isinstance(mi, dict):
        out["main_image"] = _strip_image_item(mi)
    imgs = pi.get("image_list")
    if isinstance(imgs, list):
        slim: list[dict[str, Any]] = []
        for it in imgs[:IMAGE_LIST_CAP]:
            if isinstance(it, dict):
                slim.append(_strip_image_item(it))
        if slim:
            out["image_list"] = slim
    return out


def _prune_product_for_snapshot(p: dict[str, Any]) -> dict[str, Any]:
    keep_keys = (
        "product_id",
        "biz_type",
        "product_version",
        "product_status",
        "product_desc",
        "car_base_attr",
        "car_source_attr",
        "finance_info",
        "business_info",
        "product_shop",
        "seller_info",
        "category",
        "scheme",
        "event_info",
        "sales",
    )
    o: dict[str, Any] = {}
    for k in keep_keys:
        if k in p:
            o[k] = copy.deepcopy(p[k])
    skus = p.get("sku_list")
    if isinstance(skus, list) and skus and isinstance(skus[0], dict):
        o["sku_list"] = [copy.deepcopy(skus[0])]
    pi = p.get("product_image")
    if isinstance(pi, dict):
        pruned = _prune_product_images(pi)
        if pruned:
            o["product_image"] = pruned
    return o


def _build_sophon_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    comp = data.get("component_list")
    if isinstance(comp, dict):
        comp_out: dict[str, Any] = {}
        doc = comp.get("document")
        if isinstance(doc, dict):
            doc_copy = copy.deepcopy(doc)
            csa = doc_copy.get("car_source_attr")
            if isinstance(csa, dict):
                _truncate_merchant_remark(csa, MERCHANT_REMARK_MAX)
            comp_out["document"] = doc_copy
        if isinstance(comp.get("price_analysis"), dict):
            comp_out["price_analysis"] = copy.deepcopy(comp["price_analysis"])
        if isinstance(comp.get("maintenance"), dict):
            comp_out["maintenance"] = copy.deepcopy(comp["maintenance"])
        if comp_out:
            out["component_list"] = comp_out
    prod = data.get("product")
    if isinstance(prod, dict):
        pr = _prune_product_for_snapshot(prod)
        if pr:
            out["product"] = pr
    return out


def _clamp_snapshot_size(snap: dict[str, Any]) -> dict[str, Any]:
    """Shrink snapshot until UTF-8 JSON fits ``MAX_SNAPSHOT_UTF8_BYTES``."""
    if _snapshot_utf8_size(snap) <= MAX_SNAPSHOT_UTF8_BYTES:
        return snap

    def doc_csa() -> dict[str, Any] | None:
        cl = snap.get("component_list")
        if not isinstance(cl, dict):
            return None
        doc = cl.get("document")
        if not isinstance(doc, dict):
            return None
        csa = doc.get("car_source_attr")
        return csa if isinstance(csa, dict) else None

    # Drop gallery first (URLs duplicated elsewhere).
    prod = snap.get("product")
    if isinstance(prod, dict) and isinstance(prod.get("product_image"), dict):
        pi = prod["product_image"]
        if isinstance(pi.get("image_list"), list):
            pi["image_list"] = []
        if _snapshot_utf8_size(snap) <= MAX_SNAPSHOT_UTF8_BYTES:
            return snap
        if "main_image" in pi:
            del pi["main_image"]
        if not pi:
            del prod["product_image"]
        if _snapshot_utf8_size(snap) <= MAX_SNAPSHOT_UTF8_BYTES:
            return snap

    for limit in (2000, 800, 0):
        csa = doc_csa()
        if csa is not None:
            _truncate_merchant_remark(csa, limit)
        if _snapshot_utf8_size(snap) <= MAX_SNAPSHOT_UTF8_BYTES:
            return snap

    if isinstance(prod, dict) and isinstance(prod.get("sku_list"), list):
        prod["sku_list"] = []
        if _snapshot_utf8_size(snap) <= MAX_SNAPSHOT_UTF8_BYTES:
            return snap

    if isinstance(prod, dict):
        for k in list(prod.keys()):
            if k not in ("product_id", "product_desc", "product_shop"):
                del prod[k]
        if _snapshot_utf8_size(snap) <= MAX_SNAPSHOT_UTF8_BYTES:
            return snap

    logger.warning(
        "sophon_snapshot still oversize (%d bytes), dropping snapshot",
        _snapshot_utf8_size(snap),
    )
    return {"_truncated": True, "reason": "snapshot_oversize"}


def apply_sophon_detail_to_offer(offer: Any, body: dict[str, Any]) -> bool:
    """Merge Sophon card/detail JSON into ``offer``. Returns True if payload looks valid."""
    if not isinstance(body, dict):
        return False
    st = body.get("status")
    if st is not None and st != 0:
        return False
    msg = body.get("message")
    if isinstance(msg, str) and msg and msg.lower() not in ("success", "ok"):
        return False

    data = body.get("data")
    if not isinstance(data, dict):
        return False

    comp = data.get("component_list")
    if not isinstance(comp, dict):
        return False

    doc = comp.get("document")
    if isinstance(doc, dict):
        car_src = doc.get("car_source_attr")
        if isinstance(car_src, dict):
            fr = car_src.get("first_register_time")
            ts = None
            if isinstance(fr, (int, float)) and fr > 1_000_000_000:
                ts = int(fr)
            elif isinstance(fr, str) and fr.isdigit():
                t2 = int(fr)
                if t2 > 1_000_000_000:
                    ts = t2
            if ts is not None:
                offer.first_register_timestamp = ts
                y = datetime.fromtimestamp(ts, tz=UTC).year
                if 1980 <= y <= 2100:
                    offer.year = y

    pa = comp.get("price_analysis")
    if isinstance(pa, dict):
        mp = pa.get("market_price")
        if isinstance(mp, dict):
            yv = _fen_to_yuan(mp.get("price"))
            if yv is not None:
                offer.market_valuation_yuan = yv

    maint = comp.get("maintenance")
    if isinstance(maint, dict):
        ri = maint.get("report_info")
        if isinstance(ri, dict):
            concl = ri.get("conclusion")
            if isinstance(concl, dict):
                offer.is_accident = _bool_conclusion(concl.get("is_accident"))
                offer.is_soaked = _bool_conclusion(concl.get("is_soaked"))
                offer.is_burned = _bool_conclusion(concl.get("is_burned"))
                offer.is_changed_mileage = _bool_conclusion(concl.get("is_changed_mileage"))

    product = data.get("product")
    if isinstance(product, dict):
        skus = product.get("sku_list")
        if isinstance(skus, list) and skus:
            first = skus[0]
            if isinstance(first, dict):
                sp = first.get("sku_price")
                if isinstance(sp, dict):
                    detail_price = _fen_to_yuan(sp.get("price"))
                    if detail_price is not None and detail_price > 0:
                        if offer.price_yuan is None or offer.price_yuan <= 0:
                            offer.price_yuan = detail_price
                        elif abs(offer.price_yuan - detail_price) / detail_price > 0.25:
                            offer.price_yuan = detail_price

    snap = _build_sophon_snapshot(data)
    if snap:
        offer.sophon_snapshot = _clamp_snapshot_size(snap)
    return True
