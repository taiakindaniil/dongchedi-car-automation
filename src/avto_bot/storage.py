"""Lightweight SQLite persistence for offer dedup + delivery state.

The DB has two tables:

* `offers`  — every offer we have ever seen. `first_seen_date` is what
              powers the freshness sub-score. `notified_at` is what
              guarantees we never send the same listing twice.
* `runs`    — bookkeeping for ops: when did the bot wake up, how many
              offers did it scan, how many were new, did it fail.

`aiosqlite` (rather than sqlalchemy) keeps the dependency footprint
small and the SQL legible. Concurrency is a non-issue: only the
scheduler writes, and one cycle at a time.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import aiosqlite

from .parsers.dongchedi.parser import RawOffer

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    offer_id        TEXT PRIMARY KEY,
    first_seen_date TEXT NOT NULL,
    last_seen_date  TEXT NOT NULL,
    score           REAL,
    notified_at     TEXT,
    payload_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_offers_notified ON offers(notified_at);
CREATE INDEX IF NOT EXISTS ix_offers_first    ON offers(first_seen_date);

CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    scanned     INTEGER DEFAULT 0,
    new_count   INTEGER DEFAULT 0,
    sent_count  INTEGER DEFAULT 0,
    error       TEXT
);
"""


def _today_iso() -> str:
    return date.today().isoformat()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _serialise_offer(offer: RawOffer) -> str:
    data = asdict(offer)
    data.pop("payload", None)  # raw upstream JSON is too noisy to keep
    return json.dumps(data, ensure_ascii=False)


class Storage:
    """Async-friendly thin wrapper around an `aiosqlite` connection."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> Storage:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # --- offers ---------------------------------------------------------

    async def upsert_offers(self, offers: list[RawOffer]) -> set[str]:
        """Insert / refresh offers, return ids that are new today.

        New = either the row didn't exist, or it existed but its
        `first_seen_date` already equals today (so first-seen-today set
        is idempotent across multiple runs within a single day).
        """
        assert self._db is not None
        today = _today_iso()
        new_ids: set[str] = set()
        for o in offers:
            payload = _serialise_offer(o)
            cur = await self._db.execute(
                "SELECT first_seen_date FROM offers WHERE offer_id = ?",
                (o.offer_id,),
            )
            row = await cur.fetchone()
            if row is None:
                await self._db.execute(
                    "INSERT INTO offers (offer_id, first_seen_date, "
                    "last_seen_date, payload_json) VALUES (?, ?, ?, ?)",
                    (o.offer_id, today, today, payload),
                )
                new_ids.add(o.offer_id)
            else:
                await self._db.execute(
                    "UPDATE offers SET last_seen_date = ?, payload_json = ? "
                    "WHERE offer_id = ?",
                    (today, payload, o.offer_id),
                )
                if row["first_seen_date"] == today:
                    new_ids.add(o.offer_id)
        await self._db.commit()
        return new_ids

    async def pending_for_notification(self) -> list[str]:
        """Offer ids that haven't been sent yet, oldest-first by first_seen."""
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT offer_id FROM offers WHERE notified_at IS NULL "
            "ORDER BY first_seen_date DESC, offer_id ASC"
        )
        rows = await cur.fetchall()
        return [r["offer_id"] for r in rows]

    async def mark_notified(self, offer_ids: list[str]) -> None:
        if not offer_ids or self._db is None:
            return
        await self._db.executemany(
            "UPDATE offers SET notified_at = ? WHERE offer_id = ?",
            [(_now_iso(), oid) for oid in offer_ids],
        )
        await self._db.commit()

    async def set_score(self, offer_id: str, score: float) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE offers SET score = ? WHERE offer_id = ?",
            (score, offer_id),
        )
        await self._db.commit()

    async def first_seen(self, offer_id: str) -> date | None:
        assert self._db is not None
        cur = await self._db.execute(
            "SELECT first_seen_date FROM offers WHERE offer_id = ?",
            (offer_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return date.fromisoformat(row["first_seen_date"])

    # --- runs -----------------------------------------------------------

    async def start_run(self) -> int:
        assert self._db is not None
        cur = await self._db.execute(
            "INSERT INTO runs (started_at) VALUES (?)",
            (_now_iso(),),
        )
        await self._db.commit()
        return cur.lastrowid or 0

    async def finish_run(
        self,
        run_id: int,
        *,
        scanned: int,
        new_count: int,
        sent_count: int,
        error: str | None = None,
    ) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE runs SET finished_at = ?, scanned = ?, "
            "new_count = ?, sent_count = ?, error = ? WHERE run_id = ?",
            (_now_iso(), scanned, new_count, sent_count, error, run_id),
        )
        await self._db.commit()
