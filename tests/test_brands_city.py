"""City name helpers (Chinese API → Russian display)."""

from __future__ import annotations

from avto_bot.brands import city_display_ru, city_name


def test_city_display_ru_from_chinese() -> None:
    assert city_display_ru("深圳", None) == "Шэньчжэнь"
    assert city_display_ru("北京市", None) == "Пекин"


def test_city_display_ru_strips_shi_suffix() -> None:
    assert city_display_ru("杭州市", None) == "Ханчжоу"


def test_city_display_ru_fallback_code() -> None:
    assert city_display_ru(None, 110000) == city_name(110000) == "Пекин"


def test_city_display_ru_unknown_chinese() -> None:
    assert city_display_ru("未知城", None) == "未知城"
