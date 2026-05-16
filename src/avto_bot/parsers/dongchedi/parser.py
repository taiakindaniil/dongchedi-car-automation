"""Playwright Chromium scraper for dongchedi.com used-car listings.

We **only** use Playwright-bundled Chromium (never ``channel="chrome"`` /
system Chrome). Cookies and site data persist in ``user_data_dir`` so
repeat runs keep ``msToken`` / ``a_bogus`` style flows working like a
returning visitor.

Strategy (in order of preference):

1. ``launch_persistent_context`` → same profile directory every time.

2. Open the /usedcar/… slug page. The front-end calls the PC JSON API
   ``/motor/pc/sh/sh_sku_list`` (``search_sh_sku_info_list``) and/or the
   older ``/motor/sh_go/sh_sku/list`` path. We listen for both.

3. If no XHR is captured, fall back to ``__NEXT_DATA__`` embedded JSON.

4. Intercept ``.woff2`` / ``.woff`` responses, build a PUA→digit map with
   ``fontTools`` (cached under ``data/font_map_cache`` by SHA-256 of the font
   bytes), decode ``sh_price`` / MSRP / mileage (including ``sub_title`` tail
   after ``|``) when possible, then refine from DOM ``万`` text.

``playwright_stealth`` init scripts are applied to the browser context
to reduce trivial bot fingerprinting (navigator.webdriver, chrome.*,
etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Page,
    Response,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright_stealth import Stealth
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .font_decode import (
    FontMapCache,
    apply_font_decoded_prices,
    is_private_use_codepoint,
    mileage_suffix_from_subtitle,
    parse_mileage_km_from_decoded,
    should_capture_font_response,
)

logger = logging.getLogger(__name__)


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# PC listing API (user-provided) + legacy mobile-style path.
LIST_XHR_PATTERN = re.compile(
    r"/motor/(?:pc/sh/sh_sku_list|sh_go/sh_sku/list)",
    re.IGNORECASE,
)

# After paint, digits in the *card* are real ASCII, but walking too far up the
# DOM merges many cards — the first ``3.00万`` match is often a loan teaser, not
# the sale price. We scope to a small card root, collect ``X万`` candidates,
# drop mileage / finance contexts, then take the max plausible 万 in that card.
_DOM_SALE_PRICE_WAN_JS = r"""
() => {
  const badCtx = /首付|月供|月付|月租|低至|年息|利率|万元年|\/月|\/年|立减|补贴/;
  const collect = (text) => {
    if (!text) return [];
    const t = text.replace(/\s+/g, ' ');
    const re = /(\d+(?:\.\d+)?)万/g;
    const vals = [];
    let m;
    while ((m = re.exec(t)) !== null) {
      const i = m.index;
      const ctx = t.slice(Math.max(0, i - 8), Math.min(t.length, i + m[0].length + 10));
      if (/公里/.test(ctx)) continue;
      if (badCtx.test(ctx)) continue;
      const v = parseFloat(m[1]);
      if (v > 0.05 && v < 8000) vals.push(v);
    }
    return vals;
  };
  const findCard = (a) => {
    return (
      a.closest('[class*="cell"]') ||
      a.closest('[class*="Cell"]') ||
      a.closest('[class*="item"]') ||
      a.closest('[class*="Item"]') ||
      a.closest('[class*="card"]') ||
      a.closest('[class*="Card"]') ||
      a.closest('li') ||
      a.parentElement?.parentElement?.parentElement ||
      a.parentElement?.parentElement ||
      a.parentElement
    );
  };
  const out = {};
  for (const a of document.querySelectorAll('a[href*="/usedcar/"]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/usedcar\/(\d+)\/?$/);
    if (!m) continue;
    const id = m[1];
    if (Object.prototype.hasOwnProperty.call(out, id)) continue;
    const card = findCard(a);
    if (!card) continue;
    const vals = collect(card.innerText || '');
    if (vals.length === 0) continue;
    out[id] = Math.max.apply(null, vals);
  }
  return out;
}
"""


def apply_dom_sale_price_wan(offers: list[RawOffer], id_to_wan: dict[str, float]) -> int:
    """Set ``price_yuan`` from per-card DOM map (values in 万 RMB).

    Skips the whole update if many cards share one identical 万 value — that
    usually means the DOM heuristic latched onto a repeated teaser (e.g. loan).
    """
    if len(id_to_wan) >= 4:
        unique = {round(v, 4) for v in id_to_wan.values()}
        if len(unique) == 1:
            return 0
    n = 0
    for o in offers:
        w = id_to_wan.get(str(o.offer_id))
        if w is None or w <= 0:
            continue
        yuan = w * 10_000.0
        if o.price_yuan != yuan:
            o.price_yuan = yuan
            n += 1
    return n


@dataclass(slots=True)
class RawOffer:
    """Normalised offer regardless of upstream JSON shape."""

    offer_id: str
    title: str
    series_name: str | None
    brand_id: int | None
    brand_name: str | None
    year: int | None
    mileage_km: float | None     # in km, not 万 km
    price_yuan: float | None     # in ¥, not 万 ¥
    official_price_yuan: float | None
    transfer_count: int | None
    has_inspection_report: bool
    city_name: str | None
    pub_timestamp: int | None    # unix seconds, UTC
    cover_image: str | None
    detail_url: str
    payload: dict[str, Any] = field(default_factory=dict, repr=False)


def _coerce_int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_year(raw: Any) -> int | None:
    """Pull a four-digit year out of strings like '2021款' or '2014年'."""
    if isinstance(raw, int):
        return raw if 1980 <= raw <= 2100 else None
    if isinstance(raw, str):
        m = re.search(r"(19|20)\d{2}", raw)
        if m:
            return int(m.group(0))
    return None


def _tags_have_inspection_report(tags: Any) -> bool:
    if not isinstance(tags, list):
        return False
    for t in tags:
        if isinstance(t, dict) and t.get("text") == "检测报告":
            return True
    return False


def _normalise_offer(raw: dict[str, Any]) -> RawOffer | None:
    """Map an upstream offer dict to `RawOffer`, dropping junk entries."""
    offer_id = (
        raw.get("sku_id")
        or raw.get("sh_sku_id")
        or raw.get("id")
        or raw.get("car_id")
    )
    if offer_id is None:
        return None
    offer_id = str(offer_id)

    title = (
        raw.get("title")
        or raw.get("series_name")
        or raw.get("car_name")
        or ""
    )

    series_name = raw.get("series_name") or raw.get("series") or None
    brand_name = raw.get("brand_name") or raw.get("brand") or None
    brand_id = _coerce_int(raw.get("brand_id") or raw.get("brandId"))

    year = (
        _coerce_int(raw.get("car_year"))
        or _parse_year(raw.get("year"))
        or _parse_year(raw.get("first_register_time"))
        or _parse_year(title)
    )

    mileage_km: float | None = None
    if (cm := raw.get("car_mileage")) not in (None, ""):
        v = _coerce_float(cm)
        if v is not None:
            mileage_km = v * 10000 if v < 100 else v
    if mileage_km is None and (mw := raw.get("mileage")) is not None:
        v = _coerce_float(mw)
        if v is not None:
            mileage_km = v * 10000 if v < 100 else v
    if mileage_km is None and (mk := raw.get("mileage_km") or raw.get("km")) is not None:
        mileage_km = _coerce_float(mk)
    if mileage_km is None:
        tail = mileage_suffix_from_subtitle(raw.get("sub_title"))
        if isinstance(tail, str) and tail.strip():
            t = tail.strip()
            if not any(is_private_use_codepoint(ord(c)) for c in t):
                km_plain = parse_mileage_km_from_decoded(t)
                if km_plain is not None:
                    mileage_km = km_plain

    price_yuan: float | None = None
    if (pw := raw.get("sh_price") or raw.get("price")) is not None:
        if isinstance(pw, (int, float)):
            v = float(pw)
            price_yuan = v * 10000 if v < 10000 else v
        else:
            v = _coerce_float(pw)
            if v is not None:
                price_yuan = v * 10000 if v < 10000 else v

    official_price_yuan: float | None = None
    if (ow := raw.get("official_price") or raw.get("guide_price")) is not None:
        if isinstance(ow, (int, float)):
            v = float(ow)
            official_price_yuan = v * 10000 if v < 10000 else v
        else:
            v = _coerce_float(ow)
            if v is not None:
                official_price_yuan = v * 10000 if v < 10000 else v

    transfer_count: int | None = None
    for key in ("transfer_cnt", "transfer_count", "transfer_num", "transfer_times"):
        if key in raw and raw[key] is not None and raw[key] != "":
            transfer_count = _coerce_int(raw[key])
            if transfer_count is not None:
                break

    inspection = bool(
        raw.get("report_id")
        or raw.get("inspect_report_id")
        or raw.get("has_inspect_report")
        or raw.get("is_inspect")
        or _tags_have_inspection_report(raw.get("tags"))
    )

    city_name = (
        raw.get("car_source_city_name")
        or raw.get("city_name")
        or raw.get("city")
        or raw.get("sale_city")
        or None
    )

    pub_ts = _coerce_int(
        raw.get("pub_time")
        or raw.get("publish_time")
        or raw.get("create_time")
        or raw.get("sale_time")
    )

    cover_image = (
        raw.get("image")
        or raw.get("cover_image")
        or raw.get("cover_url")
        or raw.get("image_url")
        or _first_image(raw.get("image_list"))
        or _first_image(raw.get("images"))
    )

    detail_url = (
        raw.get("detail_url")
        or raw.get("sh_url")
        or f"https://www.dongchedi.com/usedcar/{offer_id}"
    )
    if detail_url.startswith("//"):
        detail_url = "https:" + detail_url
    elif detail_url.startswith("/"):
        detail_url = "https://www.dongchedi.com" + detail_url

    return RawOffer(
        offer_id=offer_id,
        title=title,
        series_name=series_name,
        brand_id=brand_id,
        brand_name=brand_name,
        year=year,
        mileage_km=mileage_km,
        price_yuan=price_yuan,
        official_price_yuan=official_price_yuan,
        transfer_count=transfer_count,
        has_inspection_report=inspection,
        city_name=city_name,
        pub_timestamp=pub_ts,
        cover_image=cover_image,
        detail_url=detail_url,
        payload=raw,
    )


def _first_image(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("url") or first.get("image_url") or first.get("uri")
    return None


def _walk_for_offer_list(node: Any) -> list[dict[str, Any]]:
    """Recursively look for the offer list inside `__NEXT_DATA__`."""
    id_keys = {"sku_id", "sh_sku_id", "car_id"}

    def looks_like_offer_list(items: list) -> bool:
        if not items or len(items) < 2:
            return False
        return all(
            isinstance(it, dict) and any(k in it for k in id_keys) for it in items[:5]
        )

    stack: list[Any] = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for v in cur.values():
                if isinstance(v, list) and looks_like_offer_list(v):
                    return v
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            if looks_like_offer_list(cur):
                return cur
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return []


def extract_offers_from_api_payload(payload: dict[str, Any]) -> list[RawOffer]:
    """Parse one XHR JSON body into offers (used by tests and `_extract_from_xhr`)."""
    results: list[RawOffer] = []
    seen: set[str] = set()
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return results

    items: list | None = None
    for key in (
        "search_sh_sku_info_list",
        "sh_sku_info_list",
        "sku_list",
        "list",
        "result",
    ):
        candidate = data.get(key)
        if isinstance(candidate, list) and candidate:
            items = candidate
            break
    if items is None:
        si = data.get("search_info")
        if isinstance(si, dict):
            for key in ("search_sh_sku_info_list", "sh_sku_info_list", "sku_list"):
                candidate = si.get(key)
                if isinstance(candidate, list) and candidate:
                    items = candidate
                    break
    if items is None:
        items = _walk_for_offer_list(data)

    for item in items:
        if not isinstance(item, dict):
            continue
        offer = _normalise_offer(item)
        if offer is None or offer.offer_id in seen:
            continue
        seen.add(offer.offer_id)
        results.append(offer)
    return results


class DongchediParser:
    """Chromium session with a persistent profile (cookies survive restarts)."""

    def __init__(
        self,
        *,
        user_data_dir: Path,
        headless: bool = True,
        proxy: str | None = None,
        nav_timeout_ms: int = 30_000,
        font_map_cache_dir: Path | None = None,
    ) -> None:
        self._user_data_dir = Path(user_data_dir)
        self._headless = headless
        self._proxy = proxy
        self._nav_timeout_ms = nav_timeout_ms
        fmc = font_map_cache_dir if font_map_cache_dir is not None else Path("data/font_map_cache")
        self._font_map_cache = FontMapCache(Path(fmc))
        self._playwright = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> DongchediParser:
        await self._start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def _start(self) -> None:
        self._user_data_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()

        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self._user_data_dir),
            "headless": self._headless,
            # Never attach to Google Chrome — only Playwright's Chromium build.
            "viewport": {"width": 1440, "height": 900},
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "user_agent": USER_AGENT,
            "extra_http_headers": {
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        }
        if self._proxy:
            launch_kwargs["proxy"] = {"server": self._proxy}
        # Some CDNs / TLS stacks misbehave with strict verification in containers.
        launch_kwargs["ignore_https_errors"] = True

        self._context = await self._playwright.chromium.launch_persistent_context(
            **launch_kwargs,
        )
        await Stealth().apply_stealth_async(self._context)

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @asynccontextmanager
    async def _page(self):
        assert self._context is not None, "Parser not started"
        page = await self._context.new_page()
        page.set_default_timeout(max(self._nav_timeout_ms, 60_000))
        try:
            yield page
        finally:
            await page.close()

    async def _warmup_and_goto_listing(self, page: Page, url: str) -> None:
        """Navigate to listing URL; dongchedi often aborts cold deep-links.

        Mitigations: short homepage visit (cookies + referer behaviour),
        then ``goto`` with ``commit`` (survives client-side aborts that break
        ``domcontentloaded``) and fallbacks to ``load`` / ``domcontentloaded``.
        """
        home = "https://www.dongchedi.com/"
        try:
            await page.goto(home, wait_until="commit", timeout=25_000)
            await asyncio.sleep(0.4)
        except PlaywrightError as e:
            logger.debug("warmup homepage skipped: %s", e)

        nav_timeout = max(self._nav_timeout_ms, 60_000)
        last_err: Exception | None = None
        for wait_until in ("commit", "domcontentloaded", "load"):
            try:
                await page.goto(
                    url,
                    wait_until=wait_until,
                    timeout=nav_timeout,
                    referer=home,
                )
                return
            except PlaywrightError as e:
                last_err = e
                msg = str(e)
                if "ERR_ABORTED" not in msg and "net::" not in msg:
                    raise
                logger.warning(
                    "goto %s wait_until=%s failed (%s), retrying…",
                    url,
                    wait_until,
                    msg.split("\n", 1)[0],
                )
        if last_err is not None:
            raise last_err

    async def fetch_listings(self, url: str) -> list[RawOffer]:
        """Open `url` once and return all offers discovered on the page."""
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=2, min=2, max=10),
                retry=retry_if_exception_type(Exception),
                reraise=True,
            ):
                with attempt:
                    return await self._fetch_once(url)
        except RetryError as exc:  # pragma: no cover
            logger.exception("fetch_listings exhausted retries: %s", exc)
            return []
        return []

    async def _enrich_sale_prices_from_dom(self, page: Page, offers: list[RawOffer]) -> None:
        """Replace font-obfuscated JSON prices with values from painted DOM text."""
        if not offers:
            return
        await asyncio.sleep(0.9)
        try:
            raw_map = await page.evaluate(_DOM_SALE_PRICE_WAN_JS)
        except PlaywrightError as e:
            logger.warning("DOM price scrape failed: %s", e)
            return
        if not isinstance(raw_map, dict):
            return
        id_to_wan: dict[str, float] = {}
        for k, v in raw_map.items():
            if v is None:
                continue
            try:
                id_to_wan[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        n = apply_dom_sale_price_wan(offers, id_to_wan)
        if n:
            logger.info("filled %d sale prices from DOM (font-decoded text)", n)

    async def _fetch_once(self, url: str) -> list[RawOffer]:
        captured: list[dict[str, Any]] = []
        font_buffers: dict[str, bytes] = {}

        async def on_response(resp: Response) -> None:
            try:
                if LIST_XHR_PATTERN.search(resp.url):
                    data = await resp.json()
                    captured.append(data)
                    return
                if should_capture_font_response(resp.url):
                    body = await resp.body()
                    if 256 <= len(body) <= 6_000_000:
                        font_buffers[resp.url] = body
            except Exception:
                return

        async with self._page() as page:
            page.on("response", on_response)
            logger.debug("GET %s", url)
            await self._warmup_and_goto_listing(page, url)
            try:
                await page.wait_for_load_state("networkidle", timeout=12_000)
            except Exception:
                pass

            for _ in range(12):
                if captured:
                    break
                await asyncio.sleep(0.5)

            offers: list[RawOffer] = []
            seen_ids: set[str] = set()
            for payload in captured:
                for o in extract_offers_from_api_payload(payload):
                    if o.offer_id not in seen_ids:
                        seen_ids.add(o.offer_id)
                        offers.append(o)
            if offers:
                logger.info("xhr ok: %d offers from %s", len(offers), url)
                apply_font_decoded_prices(offers, font_buffers, self._font_map_cache)
                await self._enrich_sale_prices_from_dom(page, offers)
                return offers

            html = await page.content()
            offers = self._extract_from_next_data(html)
            logger.info("fallback __NEXT_DATA__: %d offers from %s", len(offers), url)
            apply_font_decoded_prices(offers, font_buffers, self._font_map_cache)
            await self._enrich_sale_prices_from_dom(page, offers)
            return offers

    def _extract_from_next_data(self, html: str) -> list[RawOffer]:
        m = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.+?)</script>',
            html,
            re.DOTALL,
        )
        if not m:
            return []
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        items = _walk_for_offer_list(payload)
        results: list[RawOffer] = []
        seen: set[str] = set()
        for item in items:
            offer = _normalise_offer(item)
            if offer is None or offer.offer_id in seen:
                continue
            seen.add(offer.offer_id)
            results.append(offer)
        return results


def offer_first_seen_iso() -> str:
    return datetime.now(UTC).date().isoformat()
