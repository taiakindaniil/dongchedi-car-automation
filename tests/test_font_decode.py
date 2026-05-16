"""Unit tests for WOFF cmap → price decoding helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from avto_bot.parsers.dongchedi.font_decode import (
    FontMapCache,
    apply_font_decoded_prices,
    build_codepoint_map_from_bytes,
    decode_mapped_string,
    mileage_suffix_from_subtitle,
    parse_mileage_km_from_decoded,
    parse_price_yuan_from_decoded,
    pick_font_char_map,
    should_capture_font_response,
)
from avto_bot.parsers.dongchedi.parser import RawOffer


def test_decode_mapped_string_and_parse_wan() -> None:
    m = {0xE463: "3", 0xE411: "8", 0xE40A: "8"}
    dec = decode_mapped_string("\ue463.\ue463\ue411\ue40a万", m)
    assert dec == "3.388万"
    assert parse_price_yuan_from_decoded(dec) == pytest.approx(33_880.0)


def test_parse_plain_wan_without_map() -> None:
    assert parse_price_yuan_from_decoded("12.5万") == pytest.approx(125_000.0)
    assert parse_price_yuan_from_decoded("3.88") == pytest.approx(38_800.0)


def test_mileage_suffix_from_subtitle() -> None:
    s = "foo | 5.2万公里"
    assert mileage_suffix_from_subtitle(s) == "5.2万公里"
    assert mileage_suffix_from_subtitle("nobar") is None


def test_parse_mileage_km_wan() -> None:
    assert parse_mileage_km_from_decoded("5.2万公里") == pytest.approx(52_000.0)
    assert parse_mileage_km_from_decoded("3.88万") == pytest.approx(38_800.0)
    assert parse_mileage_km_from_decoded("12.5") == pytest.approx(125_000.0)


def test_should_capture_motor_font_urls() -> None:
    assert should_capture_font_response(
        "https://lf3-motor.dcarstatic.com/obj/motor-fe-static/motor/pc/_next/static/media/"
        "iconfont.9a9fc2399832ab6fedb2340b19c75652.ttf"
    )
    assert should_capture_font_response(
        "https://lf6-awef.bytetos.com/obj/awesome-font/c/96fc7b50b772f52.woff2"
    )


def test_should_not_capture_random_ttf() -> None:
    assert not should_capture_font_response("https://fonts.gstatic.com/s/roboto/v30/foo.ttf")


def test_build_map_source_han_awesome_font_network() -> None:
    """Real dongchedi price font: digits = glyphOrder[1:11] after ``.notdef``."""
    try:
        from urllib.request import urlopen
    except ImportError:
        import pytest

        pytest.skip("urllib missing")
    url = "https://lf6-awef.bytetos.com/obj/awesome-font/c/96fc7b50b772f52.woff2"
    try:
        data = urlopen(url, timeout=20).read()
    except OSError:
        import pytest

        pytest.skip("network")
    if len(data) < 1000:
        import pytest

        pytest.skip("short response")
    m = build_codepoint_map_from_bytes(data)
    assert m is not None
    assert m[0xE439] == "0"
    assert m[0xE54C] == "1"
    assert m[0xE463] == "2"
    assert set(m.values()) == set("0123456789")


def test_font_map_cache_reads_disk(tmp_path: Path) -> None:
    body = b"hello-test-font"
    key = hashlib.sha256(body).hexdigest()
    mp = {"58341": "3", "58342": "8"}
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"url": "https://example.com/a.woff2", "map": mp}),
        encoding="utf-8",
    )
    cache = FontMapCache(tmp_path)
    got = cache.get_or_build("https://example.com/a.woff2", body)
    assert got is not None
    assert got[58341] == "3" and got[58342] == "8"


def test_pick_font_char_map_scores_samples(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = FontMapCache(tmp_path)
    fake_map = {0xE463: "3", 0xE411: "8", 0xE40A: "8"}

    def _fake_build(_data: bytes) -> dict[int, str] | None:
        return fake_map

    monkeypatch.setattr(
        "avto_bot.parsers.dongchedi.font_decode.build_codepoint_map_from_bytes",
        _fake_build,
    )
    fonts = {"https://cdn.example.com/n.woff2": b"x" * 300}
    samples = ["\ue463.\ue463\ue411\ue40a万"]
    chosen = pick_font_char_map(fonts, cache, samples)
    assert chosen == fake_map


def test_apply_font_decoded_prices_official_and_mileage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same PUA digit map fills MSRP and mileage when those fields are obfuscated."""
    cache = FontMapCache(tmp_path)
    # Subset matching Source Han awesome-font digit assignment (glyphOrder[1:11]).
    fake_map = {
        0xE439: "0",
        0xE54C: "1",
        0xE463: "2",
        0xE49D: "3",
        0xE41D: "4",
        0xE411: "5",
        0xE534: "6",
        0xE3EB: "7",
        0xE4E3: "8",
        0xE45D: "9",
    }

    def _fake_build(_data: bytes) -> dict[int, str] | None:
        return fake_map

    monkeypatch.setattr(
        "avto_bot.parsers.dongchedi.font_decode.build_codepoint_map_from_bytes",
        _fake_build,
    )
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
        payload={
            "sh_price": "\ue463.\ue411万",
            "official_price": "\ue4e3\ue45d万",
            "car_mileage": "\ue463.\ue534万公里",
        },
    )
    n = apply_font_decoded_prices([o], {"https://x/a.woff2": b"y" * 300}, cache)
    assert n == 3
    assert o.price_yuan == pytest.approx(25_000.0)
    assert o.official_price_yuan == pytest.approx(890_000.0)
    assert o.mileage_km == pytest.approx(26_000.0)


def test_apply_font_mileage_from_sub_title(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = FontMapCache(tmp_path)
    fake_map = {
        0xE439: "0",
        0xE54C: "1",
        0xE463: "2",
        0xE49D: "3",
        0xE41D: "4",
        0xE411: "5",
        0xE534: "6",
        0xE3EB: "7",
        0xE4E3: "8",
        0xE45D: "9",
    }

    def _fake_build(_data: bytes) -> dict[int, str] | None:
        return fake_map

    monkeypatch.setattr(
        "avto_bot.parsers.dongchedi.font_decode.build_codepoint_map_from_bytes",
        _fake_build,
    )
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
        payload={
            "sh_price": "\ue463.\ue411万",
            "sub_title": "x | \ue463.\ue534万公里",
        },
    )
    n = apply_font_decoded_prices([o], {"https://x/a.woff2": b"y" * 300}, cache)
    assert n >= 2
    assert o.mileage_km == pytest.approx(26_000.0)


def test_apply_font_decoded_prices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = FontMapCache(tmp_path)
    fake_map = {0xE463: "3", 0xE411: "8", 0xE40A: "8"}

    def _fake_build(_data: bytes) -> dict[int, str] | None:
        return fake_map

    monkeypatch.setattr(
        "avto_bot.parsers.dongchedi.font_decode.build_codepoint_map_from_bytes",
        _fake_build,
    )
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
        payload={"sh_price": "\ue463.\ue463\ue411\ue40a万"},
    )
    n = apply_font_decoded_prices([o], {"https://x/a.woff2": b"y" * 300}, cache)
    assert n == 1
    assert o.price_yuan == pytest.approx(33_880.0)
