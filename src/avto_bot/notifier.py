"""Telegram delivery: a digest header plus one card per offer.

Uses aiogram 3.x. We send a digest header first so the user immediately
sees "today we picked 7 cars for you" rather than a wall of cards. Then
each scored offer becomes its own message: `sendPhoto` with HTML caption
when a cover image is available, plain HTML `sendMessage` otherwise.

A soft rate-limit (`sleep 0.6 s` between sends) keeps us well inside
Telegram's per-chat limits (~30 msg/sec global, but 1 msg/sec is the
practical recommendation for the same chat).
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import date

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from .brands import brand_name, city_display_ru
from .config import ScoringWeights
from .scorer import ScoredOffer

logger = logging.getLogger(__name__)

SEND_INTERVAL_SEC = 0.6

# Custom Telegram emoji (premium / emoji pack). Fallback glyph inside <tg-emoji>.
_EMOJI_PRICE = ("5992430854909989581", "🪙")
_EMOJI_MSRP = ("5778318458802409852", "💰")
_EMOJI_MILEAGE = ("5778550614669660455", "⏲")
_EMOJI_OWNERS = ("5920344347152224466", "👤")
_EMOJI_REPORT = ("5778423822940114949", "🛡")
_EMOJI_CITY = ("5870718761710915573", "📍")
_EMOJI_LINK = ("5877465816030515018", "🔗")
_EMOJI_BELOW_MEDIAN = ("5312241539987020022", "🔥")

_BREAKDOWN_ROWS: tuple[tuple[str, str], ...] = (
    ("freshness", "Свежесть"),
    ("price_value", "Цена к медиане"),
    ("low_km", "Пробег"),
    ("owners", "Владельцы"),
    ("inspection", "Отчёт"),
    ("age", "Возраст"),
    ("premium", "Премиум-сегмент"),
)


def _tg_emoji(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def _format_yuan_line(yuan: float | None) -> str:
    """Integer yuan with space thousands, e.g. ``166 800 ¥``."""
    if yuan is None or yuan <= 0:
        return "цена скрыта"
    n = int(round(float(yuan)))
    return f"{n:,} ¥".replace(",", " ")


def _format_km(km: float | None) -> str:
    if km is None or km < 0:
        return "пробег ?"
    if km >= 1_000:
        return f"{km / 1_000:.0f} тыс. км"
    return f"{km:.0f} км"


def _weight_for_key(weights: ScoringWeights, key: str) -> float:
    return getattr(weights, key)


def _score_breakdown_lines(scored: ScoredOffer, weights: ScoringWeights) -> list[str]:
    """Human-readable breakdown: sub-score [0,1], YAML weight, product, then total."""
    bd = scored.breakdown
    total_w = weights.total() or 1.0
    lines: list[str] = [
        "<i>Разбор рейтинга</i> (подскоры 0–1, веса из YAML):",
    ]
    acc = 0.0
    n = len(_BREAKDOWN_ROWS)
    for i, (key, label) in enumerate(_BREAKDOWN_ROWS):
        b = float(bd.get(key, 0.0))
        w = _weight_for_key(weights, key)
        prod = w * b
        acc += prod
        branch = "├" if i < n - 1 else "└"
        esc = html.escape(label)
        lines.append(f"{branch} {esc}: {b:.2f} × {w:.2f} → {prod:.2f}")
    lines.append(
        f"<b>Итог</b>: {acc:.2f} / {total_w:.2f} = <b>{scored.score:.2f}</b>"
    )
    return lines


def render_card(
    scored: ScoredOffer,
    *,
    city_code: int | None,
    index: int = 1,
    show_score_breakdown: bool = False,
    scoring_weights: ScoringWeights | None = None,
) -> str:
    """Build an HTML caption / message body for one offer (tree + custom tg-emoji)."""
    o = scored.offer
    title_parts: list[str] = []
    if o.brand_name:
        title_parts.append(o.brand_name)
    elif o.brand_id:
        title_parts.append(brand_name(o.brand_id))
    if o.series_name and o.series_name not in title_parts:
        title_parts.append(o.series_name)
    if o.year:
        title_parts.append(str(o.year))
    title = " ".join(title_parts) or o.title or f"Авто #{o.offer_id}"

    location = city_display_ru(o.city_name, city_code)
    report = "✅" if o.has_inspection_report else "❌"
    owners = (
        "0"
        if o.transfer_count == 0
        else str(o.transfer_count)
        if o.transfer_count is not None
        else "?"
    )

    e_price = _tg_emoji(*_EMOJI_PRICE)
    e_msrp = _tg_emoji(*_EMOJI_MSRP)
    e_km = _tg_emoji(*_EMOJI_MILEAGE)
    e_own = _tg_emoji(*_EMOJI_OWNERS)
    e_rep = _tg_emoji(*_EMOJI_REPORT)
    e_pin = _tg_emoji(*_EMOJI_CITY)
    e_link = _tg_emoji(*_EMOJI_LINK)
    e_fire = _tg_emoji(*_EMOJI_BELOW_MEDIAN)

    head = f"{index}. {html.escape(title)} ({scored.score:.2f})"
    body_lines = [
        f"┠ {e_price} Цена: {html.escape(_format_yuan_line(o.price_yuan))}",
        f"┠ {e_msrp} Цена за новую: {html.escape(_format_yuan_line(o.official_price_yuan))}",
        f"┠ {e_km} Пробег: {html.escape(_format_km(o.mileage_km))}",
        f"┠ {e_own} Было владельцев: {html.escape(owners)}",
        f"┠ {e_rep} Отчет: {report}",
        f"└ {e_pin} Город: {html.escape(location)}",
    ]
    pbm = scored.price_below_median_pct
    if pbm is not None and pbm > 0:
        pct_txt = f"{pbm:.0f}" if pbm >= 10 else f"{pbm:.1f}".rstrip("0").rstrip(".")
        body_lines.append(" ")
        body_lines.append(f"{e_fire} Цена на {pct_txt}% ниже медианы")
    if show_score_breakdown and scoring_weights is not None:
        body_lines.append("")
        body_lines.extend(_score_breakdown_lines(scored, scoring_weights))
    body_lines.extend(
        [
            "",
            f"{e_link} <a href=\"{html.escape(o.detail_url, quote=True)}\">Открыть на Dongchedi</a>",
        ]
    )
    return f"<b>{head}</b>\n" + "\n".join(body_lines)


def render_digest_header(
    *,
    today: date,
    new_count: int,
    sending_count: int,
    scanned: int,
) -> str:
    return (
        f"<b>🚗 Авто-дайджест · {today.isoformat()}</b>\n"
        f"Просканировано: <b>{scanned}</b>  ·  Новых сегодня: <b>{new_count}</b>\n"
        f"Отправляю топ-<b>{sending_count}</b> по скорингу."
    )


class TelegramNotifier:
    """Owns the aiogram Bot session and applies a soft rate limit."""

    def __init__(self, token: str, chat_id: str, *, city_code: int | None = None) -> None:
        if not token:
            raise ValueError("Telegram BOT_TOKEN is empty — set it in .env")
        if not chat_id:
            raise ValueError("Telegram CHAT_ID is empty — set it in .env")
        self._chat_id = chat_id
        self._city_code = city_code
        self._bot = Bot(
            token=token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    async def close(self) -> None:
        await self._bot.session.close()

    async def __aenter__(self) -> TelegramNotifier:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _send_text(self, text: str) -> None:
        await self._safe(
            self._bot.send_message,
            chat_id=self._chat_id,
            text=text,
            disable_web_page_preview=True,
        )

    async def _send_photo(self, photo_url: str, caption: str) -> None:
        try:
            await self._safe(
                self._bot.send_photo,
                chat_id=self._chat_id,
                photo=photo_url,
                caption=caption,
            )
        except TelegramAPIError as e:
            # Some image URLs from dongchedi are CDN-signed and may 403;
            # in that case we still want the listing to land — as text.
            logger.warning("sendPhoto failed (%s), falling back to text", e)
            await self._send_text(caption)

    async def _safe(self, func, **kwargs):
        for _ in range(3):
            try:
                return await func(**kwargs)
            except TelegramRetryAfter as e:
                logger.warning("Telegram flood, sleeping %ss", e.retry_after)
                await asyncio.sleep(float(e.retry_after) + 0.5)
            except TelegramAPIError:
                raise
        raise RuntimeError("Telegram retry budget exhausted")

    async def send_digest(
        self,
        *,
        scored: list[ScoredOffer],
        scanned: int,
        new_count: int,
        with_photos: bool,
        show_score_breakdown: bool = False,
        scoring_weights: ScoringWeights | None = None,
    ) -> int:
        """Send a digest header followed by one card per scored offer.

        Returns the number of offer messages actually delivered (header
        is not counted).
        """
        header = render_digest_header(
            today=date.today(),
            new_count=new_count,
            sending_count=len(scored),
            scanned=scanned,
        )
        await self._send_text(header)

        if not scored:
            await self._send_text(
                "Сегодня новых подходящих объявлений не найдено. "
                "Завтра проверим ещё раз."
            )
            return 0

        sent = 0
        for i, s in enumerate(scored, start=1):
            caption = render_card(
                s,
                city_code=self._city_code,
                index=i,
                show_score_breakdown=show_score_breakdown,
                scoring_weights=scoring_weights,
            )
            if with_photos and s.offer.cover_image:
                await self._send_photo(s.offer.cover_image, caption)
            else:
                await self._send_text(caption)
            sent += 1
            await asyncio.sleep(SEND_INTERVAL_SEC)
        return sent

    async def send_error(self, message: str) -> None:
        try:
            await self._send_text(f"⚠️ <b>avto-bot</b>: {html.escape(message)}")
        except Exception:  # noqa: BLE001
            logger.exception("Failed to deliver error notice")
