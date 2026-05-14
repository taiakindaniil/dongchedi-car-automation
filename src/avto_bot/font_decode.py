"""Decode dongchedi-style ``sh_price`` strings that use a custom webfont (PUA).

Intercepted ``.woff2`` bytes are parsed with ``fontTools``. We first try to
match PUA glyphs to real digits by **outline hash** (same technique as the
site: PUA codepoints point at glyphs shaped like ``0``–``9``). If the font
embeds ASCII or full-width digits, those hashes become the reference table.

If that fails, we fall back to a weak heuristic: exactly ten PUA codepoints in
the cmap mapped in ascending Unicode order to ``0``…``9`` (works for some
subset fonts).

The listing page also loads **DCD_Icon** (``iconfont.*`` on ``dcarstatic.com``):
~270 PUA icon slots — automatic digit decode usually fails there.

**Prices** on PC often use ``font-family: pORE5nVm2QTpLQtk`` → awesome-font
``96fc7b50…woff2`` (**Source Han Sans SC** subset). Those files map digits ``0``–
``9`` to the first ten glyphs after ``.notdef`` (``glyphOrder[1:11]``); we
reverse that via cmap. Weights 400/500/700 share the same PUA→digit mapping.

Mapping is cached on disk keyed by ``sha256(font_bytes)`` (stable even when
CDN query strings rotate); the JSON sidecar stores the original URL for
debugging.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from fontTools.pens.hashPointPen import HashPointPen
from fontTools.ttLib import TTFont

logger = logging.getLogger(__name__)

_WAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万")

# Buffer ``.ttf`` / ``.otf`` only from motor / ByteDance CDNs (e.g. ``iconfont…ttf``);
# woff/woff2 stay broad so small digit-subset fonts from any host still count.
_MOTOR_FONT_URL_MARKERS: tuple[str, ...] = (
    "dcarstatic.com",
    "bytetos.com",
    "byteimg.com",
    "pstatp.com",
    "toutiaopic.com",
    "snssdk.com",
    "bdstatic.com",
    "byted-static.com",
)


def should_capture_font_response(url: str) -> bool:
    """Whether to keep response bytes for ``font_decode`` (parser applies size limits)."""
    low = url.lower()
    if ".woff2" in low or low.endswith(".woff"):
        return True
    if low.endswith((".ttf", ".otf")) and any(m in low for m in _MOTOR_FONT_URL_MARKERS):
        return True
    return False


def is_private_use_codepoint(code: int) -> bool:
    if 0xE000 <= code <= 0xF8FF:
        return True
    if 0xF0000 <= code <= 0xFFFFD:
        return True
    if 0x100000 <= code <= 0x10FFFD:
        return True
    return False


def _glyph_hash(glyph_set: Any, glyph_name: str) -> str | None:
    try:
        g = glyph_set[glyph_name]
    except KeyError:
        return None
    pen = HashPointPen(g.width, glyph_set)
    try:
        g.drawPoints(pen)
    except Exception:
        return None
    h = pen.hash
    return h if isinstance(h, str) else str(h)


def _collect_reference_hashes(glyph_set: Any, cmap: dict[int, str]) -> dict[str, str]:
    """Map outline-hash → ASCII digit / dot for glyphs that look like plain text."""
    ref: dict[str, str] = {}
    for digit in "0123456789":
        code = ord(digit)
        if code not in cmap:
            continue
        h = _glyph_hash(glyph_set, cmap[code])
        if h:
            ref[h] = digit
    for i in range(10):
        code = 0xFF10 + i  # fullwidth ０–９
        if code not in cmap:
            continue
        h = _glyph_hash(glyph_set, cmap[code])
        if h:
            ref[h] = str(i)
    for dot_ch in (".", "·", "\uFF0E"):
        code = ord(dot_ch)
        if code not in cmap:
            continue
        h = _glyph_hash(glyph_set, cmap[code])
        if h:
            ref[h] = "."
    return ref


def _map_pua_by_outline(
    cmap: dict[int, str], glyph_set: Any, ref: dict[str, str]
) -> dict[int, str]:
    out: dict[int, str] = {}
    wan_ord = ord("万")
    wan_hash: str | None = None
    if wan_ord in cmap:
        wan_hash = _glyph_hash(glyph_set, cmap[wan_ord])
    for code, gname in cmap.items():
        if not is_private_use_codepoint(code):
            continue
        gh = _glyph_hash(glyph_set, gname)
        if not gh:
            continue
        if gh in ref:
            out[code] = ref[gh]
        elif wan_hash and gh == wan_hash:
            out[code] = "万"
    return out


def _fallback_ten_pua_sorted(cmap: dict[int, str]) -> dict[int, str] | None:
    pua = sorted(c for c in cmap if is_private_use_codepoint(c))
    if len(pua) != 10:
        return None
    logger.warning(
        "font_decode: weak PUA order fallback (%d codepoints; no ASCII digits in font)",
        len(pua),
    )
    return {c: str(i) for i, c in enumerate(pua)}


def _font_name_blob_lower(font: TTFont) -> str:
    parts: list[str] = []
    if "name" not in font:
        return ""
    raw = font["name"].names
    seq = raw.values() if isinstance(raw, dict) else raw
    for rec in seq:
        try:
            parts.append(rec.toUnicode())
        except Exception:
            continue
    return " ".join(parts).lower()


def _build_awesome_source_han_digit_map(font: TTFont) -> dict[int, str] | None:
    """Digit map for ByteDance ``awesome-font`` Source Han subsets (dongchedi price font).

    The CSS stack ``pORE5nVm2QTpLQtk`` points at ``96fc7b50b772f52*.woff2``.
    In these files, glyphs at ``glyphOrder[1]`` … ``glyphOrder[10]`` are the
    digit shapes ``0``…``9``; cmap maps one PUA codepoint per digit glyph.
    """
    names = _font_name_blob_lower(font)
    if "source" not in names or "han" not in names:
        return None
    cmap = font.getBestCmap()
    order = font.getGlyphOrder()
    if len(cmap) < 150 or len(order) < 12:
        return None
    if order[0] != ".notdef":
        return None
    out: dict[int, str] = {}
    for i in range(10):
        gname = order[1 + i]
        cps = [cp for cp, gn in cmap.items() if gn == gname]
        if not cps:
            return None
        if not all(is_private_use_codepoint(cp) for cp in cps):
            return None
        for cp in cps:
            out[cp] = str(i)
    if set(out.values()) != set("0123456789"):
        return None
    return out


def build_codepoint_map_from_bytes(data: bytes) -> dict[int, str] | None:
    """Return ``{unicode_codepoint: '0'..'9'|'.'|'万'}`` or ``None`` if unusable."""
    try:
        font = TTFont(BytesIO(data))
    except Exception as e:
        logger.debug("font_decode: TTFont parse failed: %s", e)
        return None
    cmap = font.getBestCmap()
    if not cmap:
        return None
    glyph_set = font.getGlyphSet()
    ref = _collect_reference_hashes(glyph_set, cmap)
    mapped = _map_pua_by_outline(cmap, glyph_set, ref)
    digit_like = sum(1 for ch in mapped.values() if ch.isdigit())
    if digit_like >= 8:
        return mapped
    if mapped and digit_like >= 3:
        return mapped
    fb = _fallback_ten_pua_sorted(cmap)
    if fb:
        return fb
    awesome = _build_awesome_source_han_digit_map(font)
    if awesome:
        logger.info("font_decode: Source Han awesome-font digit map (%d codepoints)", len(awesome))
        return awesome
    return None


def decode_mapped_string(s: str, char_map: dict[int, str]) -> str:
    parts: list[str] = []
    for ch in s:
        o = ord(ch)
        if o in char_map:
            parts.append(char_map[o])
        elif ch in ".0123456789":
            parts.append(ch)
        elif ch in "万":
            parts.append("万")
        else:
            if not is_private_use_codepoint(o):
                parts.append(ch)
    return "".join(parts)


def mileage_suffix_from_subtitle(sub_title: Any) -> str | None:
    """Right-hand side of ``sub_title`` after the first ``|`` (odometer text on dongchedi)."""
    if not isinstance(sub_title, str) or "|" not in sub_title:
        return None
    _, _, tail = sub_title.partition("|")
    t = tail.strip()
    return t if t else None


def parse_price_yuan_from_decoded(decoded: str) -> float | None:
    """Interpret decoded text as 万 RMB → yuan."""
    t = decoded.replace(" ", "").strip()
    if not t:
        return None
    m = _WAN_RE.search(t)
    if m:
        try:
            return float(m.group(1)) * 10_000.0
        except ValueError:
            return None
    try:
        v = float(t)
    except ValueError:
        return None
    if 0 < v < 10_000:
        return v * 10_000.0
    if v >= 10_000:
        return v
    return None


_MILES_WAN_KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万\s*公里")


def parse_mileage_km_from_decoded(decoded: str) -> float | None:
    """Decode 万公里 / 万 km strings to km (same ``_normalise_offer`` scaling)."""
    t = decoded.replace(" ", "").strip()
    if not t:
        return None
    m = _MILES_WAN_KM_RE.search(t)
    if m:
        try:
            km = float(m.group(1)) * 10_000.0
        except ValueError:
            return None
        return km if km <= 2_000_000 else None
    m = _WAN_RE.search(t)
    if m:
        try:
            wan = float(m.group(1))
        except ValueError:
            return None
        km = wan * 10_000.0
        return km if wan <= 200 and km <= 2_000_000 else None
    try:
        v = float(t)
    except ValueError:
        return None
    if 0 < v < 300:
        km = v * 10_000.0
        return km if km <= 2_000_000 else None
    if 300 <= v <= 2_000_000:
        return v
    return None


def _decoded_plausible_for_font_pick(decoded: str) -> bool:
    """True if decoded text looks like a real price (万 ¥) or odometer (万 km)."""
    y = parse_price_yuan_from_decoded(decoded)
    if y is not None and 1_000 <= y <= 500_000_000:
        return True
    km = parse_mileage_km_from_decoded(decoded)
    if km is not None and 50 <= km <= 2_000_000:
        return True
    return False


def _sample_has_pua(samples: list[str]) -> bool:
    for s in samples:
        if not s or not isinstance(s, str):
            continue
        if any(is_private_use_codepoint(ord(c)) for c in s):
            return True
    return False


@dataclass
class FontMapCache:
    """In-memory + JSON disk cache of ``sha256(font) → char map``."""

    cache_dir: Path
    _mem: dict[str, dict[int, str]] = field(default_factory=dict, repr=False)

    def get_or_build(self, url: str, body: bytes) -> dict[int, str] | None:
        key = hashlib.sha256(body).hexdigest()
        if key in self._mem:
            return self._mem[key]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                mp = {int(k): str(v) for k, v in data.get("map", {}).items()}
                if mp:
                    self._mem[key] = mp
                    return mp
            except (OSError, ValueError, TypeError) as e:
                logger.warning("font map cache read failed %s: %s", path, e)
        built = build_codepoint_map_from_bytes(body)
        if not built:
            return None
        self._mem[key] = built
        try:
            payload = {
                "url": url,
                "map": {str(k): v for k, v in built.items()},
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=True),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("font map cache write failed %s: %s", path, e)
        return built


def pick_font_char_map(
    font_url_to_bytes: dict[str, bytes],
    cache: FontMapCache,
    sample_strings: list[str],
) -> dict[int, str] | None:
    """Pick a cmap that decodes samples to plausible 万-prices or 万-km odometers."""
    if not font_url_to_bytes or not _sample_has_pua(sample_strings):
        return None
    best: dict[int, str] | None = None
    best_score = 0
    for url, body in font_url_to_bytes.items():
        m = cache.get_or_build(url, body)
        if not m:
            continue
        score = 0
        for s in sample_strings[:48]:
            if not isinstance(s, str):
                continue
            dec = decode_mapped_string(s, m)
            if _decoded_plausible_for_font_pick(dec):
                score += 1
        if score > best_score:
            best_score = score
            best = m
    if best is None or best_score == 0:
        return None
    return best


def _pua_strings_from_raw(raw: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in (
        "sh_price",
        "price",
        "official_price",
        "guide_price",
        "car_mileage",
        "mileage",
    ):
        v = raw.get(key)
        if isinstance(v, str) and v.strip() and any(is_private_use_codepoint(ord(c)) for c in v):
            out.append(v)
    st = raw.get("sub_title")
    if isinstance(st, str) and st.strip():
        if any(is_private_use_codepoint(ord(c)) for c in st):
            out.append(st)
        tail = mileage_suffix_from_subtitle(st)
        if (
            isinstance(tail, str)
            and tail.strip()
            and any(is_private_use_codepoint(ord(c)) for c in tail)
            and tail not in out
        ):
            out.append(tail.strip())
    return out


def apply_font_decoded_prices(
    offers: list[Any],
    font_url_to_bytes: dict[str, bytes],
    cache: FontMapCache,
) -> int:
    """Fill ``price_yuan``, ``official_price_yuan``, ``mileage_km`` from obfuscated strings.

    Mileage is also read from ``sub_title`` text after the first ``|`` when
    ``car_mileage`` / ``mileage`` are empty (dongchedi list cards).
    """
    if not offers or not font_url_to_bytes:
        return 0
    samples: list[str] = []
    for o in offers:
        raw = o.payload
        if isinstance(raw, dict):
            samples.extend(_pua_strings_from_raw(raw))
    seen: set[str] = set()
    uniq: list[str] = []
    for s in samples:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    samples = uniq[:48]
    cmap = pick_font_char_map(font_url_to_bytes, cache, samples)
    if not cmap:
        return 0
    n = 0
    for o in offers:
        raw = o.payload
        if not isinstance(raw, dict):
            continue
        sp = raw.get("sh_price") or raw.get("price")
        if isinstance(sp, str) and sp.strip() and any(is_private_use_codepoint(ord(c)) for c in sp):
            y = parse_price_yuan_from_decoded(decode_mapped_string(sp, cmap))
            if y is not None and y > 0 and o.price_yuan != y:
                o.price_yuan = y
                n += 1
        for ok in ("official_price", "guide_price"):
            ov = raw.get(ok)
            if (
                not isinstance(ov, str)
                or not ov.strip()
                or not any(is_private_use_codepoint(ord(c)) for c in ov)
            ):
                continue
            oy = parse_price_yuan_from_decoded(decode_mapped_string(ov, cmap))
            if oy is not None and oy > 0 and o.official_price_yuan != oy:
                o.official_price_yuan = oy
                n += 1
        for mk in ("car_mileage", "mileage"):
            mv = raw.get(mk)
            if (
                not isinstance(mv, str)
                or not mv.strip()
                or not any(is_private_use_codepoint(ord(c)) for c in mv)
            ):
                continue
            km = parse_mileage_km_from_decoded(decode_mapped_string(mv, cmap))
            if km is not None and km > 0 and o.mileage_km != km:
                o.mileage_km = km
                n += 1
        if o.mileage_km is None:
            tail = mileage_suffix_from_subtitle(raw.get("sub_title"))
            if (
                isinstance(tail, str)
                and tail.strip()
                and any(is_private_use_codepoint(ord(c)) for c in tail)
            ):
                km = parse_mileage_km_from_decoded(decode_mapped_string(tail.strip(), cmap))
                if km is not None and km > 0:
                    o.mileage_km = km
                    n += 1
    if n:
        logger.info(
            "filled %d numeric fields from intercepted webfont cmap (price / MSRP / mileage)",
            n,
        )
    return n

