"""Entry point: orchestrates parser → storage → scorer → notifier.

CLI:
    python -m avto_bot run     # one cycle and exit
    python -m avto_bot serve   # APScheduler cron, runs forever
    python -m avto_bot test-tg # ping the chat to verify the bot setup

`run` is also what the cron job inside `serve` invokes on every tick,
so they share a single code path.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from datetime import date, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import AppConfig, Settings, load_app_config
from .notifier import TelegramNotifier
from .parser import DongchediParser, RawOffer
from .scorer import score_offers
from .storage import Storage
from .url_builder import iter_urls

logger = logging.getLogger("avto_bot")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)


async def _yesterday_ids(storage: Storage) -> set[str]:
    """Pull offer ids whose first_seen was yesterday — used by scorer."""
    yday = (date.today() - timedelta(days=1)).isoformat()
    assert storage._db is not None
    cur = await storage._db.execute(
        "SELECT offer_id FROM offers WHERE first_seen_date = ?",
        (yday,),
    )
    rows = await cur.fetchall()
    return {r["offer_id"] for r in rows}


async def run_once(settings: Settings, app_cfg: AppConfig) -> dict[str, Any]:
    """One full cycle. Returns a small stats dict for logging / tests."""
    storage = Storage(settings.db_path)
    await storage.connect()
    run_id = await storage.start_run()

    scanned = 0
    new_today_total: set[str] = set()
    sent = 0
    error: str | None = None
    all_offers: dict[str, RawOffer] = {}

    try:
        async with DongchediParser(
            user_data_dir=settings.playwright_user_data_dir,
            headless=settings.headless,
            proxy=settings.http_proxy,
            font_map_cache_dir=settings.font_map_cache_dir,
        ) as parser:
            for page, url in iter_urls(app_cfg.filters):
                logger.info("Fetching page %d  %s", page, url)
                offers = await parser.fetch_listings(url)
                scanned += len(offers)
                for o in offers:
                    all_offers[o.offer_id] = o  # dedupe across pages/brands

        if app_cfg.filters.inspected_only:
            all_offers = {
                k: v for k, v in all_offers.items() if v.has_inspection_report
            }

        if not all_offers:
            logger.warning("No offers parsed; nothing to do this cycle")
        else:
            new_today_total = await storage.upsert_offers(list(all_offers.values()))
            yday_ids = await _yesterday_ids(storage)
            scored = score_offers(
                list(all_offers.values()),
                weights=app_cfg.scoring_weights,
                new_today_ids=new_today_total,
                yesterday_ids=yday_ids,
            )
            for s in scored:
                await storage.set_score(s.offer.offer_id, s.score)

            pending = set(await storage.pending_for_notification())
            to_send = [
                s
                for s in scored
                if s.offer.offer_id in pending
                and s.score >= app_cfg.notify.min_score
            ][: app_cfg.notify.top_n_per_day]

            async with TelegramNotifier(
                settings.bot_token,
                settings.chat_id,
                city_code=app_cfg.filters.city,
            ) as notifier:
                sent = await notifier.send_digest(
                    scored=to_send,
                    scanned=scanned,
                    new_count=len(new_today_total),
                    with_photos=app_cfg.notify.send_photos,
                )
                await storage.mark_notified([s.offer.offer_id for s in to_send])
    except Exception as exc:  # noqa: BLE001 — record failure for ops
        error = repr(exc)
        logger.exception("run_once failed")
        try:
            async with TelegramNotifier(
                settings.bot_token,
                settings.chat_id,
                city_code=app_cfg.filters.city,
            ) as notifier:
                await notifier.send_error(error)
        except Exception:
            logger.exception("Could not notify Telegram about the failure")
    finally:
        await storage.finish_run(
            run_id,
            scanned=scanned,
            new_count=len(new_today_total),
            sent_count=sent,
            error=error,
        )
        await storage.close()

    stats = {
        "scanned": scanned,
        "new": len(new_today_total),
        "sent": sent,
        "error": error,
    }
    logger.info("cycle done: %s", stats)
    return stats


async def serve(settings: Settings, app_cfg: AppConfig) -> None:
    """Long-running mode for Docker / launchd."""
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    trigger = CronTrigger.from_crontab(
        app_cfg.notify.schedule_cron, timezone=settings.timezone
    )

    async def _tick() -> None:
        # Reload YAML on every tick so the user can edit filters without
        # restarting the container.
        cfg = load_app_config(settings.config_path)
        await run_once(settings, cfg)

    scheduler.add_job(_tick, trigger=trigger, id="daily", coalesce=True, max_instances=1)
    scheduler.start()
    logger.info(
        "Scheduler started, cron=%r tz=%s; next fire: %s",
        app_cfg.notify.schedule_cron,
        settings.timezone,
        scheduler.get_job("daily").next_run_time,
    )

    stop = asyncio.Event()

    def _on_signal(*_: object) -> None:
        logger.info("Shutdown requested")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass  # Windows / non-mainthread

    await stop.wait()
    scheduler.shutdown(wait=False)


async def test_telegram(settings: Settings) -> None:
    async with TelegramNotifier(settings.bot_token, settings.chat_id) as n:
        await n._send_text("✅ avto-bot подключён к Telegram. Готов работать.")


def cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="avto-bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Single fetch/score/notify cycle")
    sub.add_parser("serve", help="Run scheduler in foreground")
    sub.add_parser("test-tg", help="Send a ping to Telegram")

    args = parser.parse_args(argv)

    settings = Settings()
    app_cfg = load_app_config(settings.config_path)
    _configure_logging(settings.log_level)

    if args.cmd == "run":
        asyncio.run(run_once(settings, app_cfg))
    elif args.cmd == "serve":
        try:
            asyncio.run(serve(settings, app_cfg))
        except KeyboardInterrupt:
            pass
    elif args.cmd == "test-tg":
        asyncio.run(test_telegram(settings))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    cli()
