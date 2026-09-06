"""
SQLite data layer. All persistent state lives in data/lidl.db.

Call init_db() once at startup (idempotent).
"""
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "lidl.db"


def _conn() -> sqlite3.Connection:
    """Open and return a sqlite3 connection with row_factory set. Caller uses it as a context manager."""
    try:
        DB_PATH.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        logger.exception("Failed to open database at %s", DB_PATH)
        raise


def init_db() -> None:
    """Create all tables and indexes (idempotent). Call once at startup."""
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS receipts (
                id          TEXT PRIMARY KEY,
                date        TEXT NOT NULL,
                store_name  TEXT,
                store_id    TEXT,
                total       REAL,
                currency    TEXT,
                synced_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS items (
                id          TEXT PRIMARY KEY,
                receipt_id  TEXT REFERENCES receipts(id),
                name        TEXT,
                quantity    REAL,
                price       REAL,
                discount    REAL DEFAULT 0,
                art_id      TEXT
            );

            CREATE TABLE IF NOT EXISTS offers (
                id              TEXT PRIMARY KEY,
                title           TEXT,
                description     TEXT,
                offer_price     REAL,
                original_price  REAL,
                discount_msg    TEXT,
                valid_from      TEXT,
                valid_until     TEXT,
                product_ids     TEXT,
                fetched_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS coupons (
                id                  TEXT PRIMARY KEY,
                promotion_id        TEXT,
                title               TEXT,
                discount_title      TEXT,
                discount_desc       TEXT,
                valid_from          TEXT,
                valid_until         TEXT,
                is_activated        INTEGER DEFAULT 0,
                article_ids         TEXT,
                fetched_at          TEXT
            );
        """)
        # Migrate older tables that lack newer columns (ignore if already present)
        for table, col in (
            ("offers", "original_price REAL"), ("offers", "discount_msg TEXT"),
            ("offers", "valid_from TEXT"), ("offers", "valid_until TEXT"),
            ("offers", "product_ids TEXT"),
            ("coupons", "article_ids TEXT"),
            ("items", "art_id TEXT"),
        ):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
            except Exception:
                logger.debug("Column already exists in %s: %s", table, col)

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_items_name        ON items (name);
            CREATE INDEX IF NOT EXISTS idx_items_receipt_id  ON items (receipt_id);
            CREATE INDEX IF NOT EXISTS idx_receipts_date     ON receipts (date);
            CREATE INDEX IF NOT EXISTS idx_offers_valid_until  ON offers (valid_until);
            CREATE INDEX IF NOT EXISTS idx_coupons_valid_until ON coupons (valid_until);
        """)


# ── write ────────────────────────────────────────────────────────────────────

def upsert_receipt(receipt: dict) -> None:
    """Insert or update a receipt header row."""
    now = datetime.now(UTC).isoformat()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO receipts (id, date, store_name, store_id, total, currency, synced_at)
            VALUES (:id, :date, :store_name, :store_id, :total, :currency, :synced_at)
            ON CONFLICT(id) DO UPDATE SET
                store_name = excluded.store_name,
                total      = excluded.total,
                synced_at  = excluded.synced_at
            """,
            {**receipt, "synced_at": now},
        )


def upsert_items(items: list[dict]) -> None:
    """Insert item rows for a receipt; skips rows that already exist (ON CONFLICT DO NOTHING)."""
    if not items:
        return
    with _conn() as conn:
        conn.executemany(
            """
            INSERT INTO items (id, receipt_id, name, quantity, price, discount, art_id)
            VALUES (:id, :receipt_id, :name, :quantity, :price, :discount, :art_id)
            ON CONFLICT(id) DO NOTHING
            """,
            [{"art_id": "", **it} for it in items],
        )


def upsert_offers(offers: list[dict]) -> None:
    """Replace all offer rows with the latest sync results (DELETE then INSERT)."""
    if not offers:
        return
    now = datetime.now(UTC).isoformat()
    with _conn() as conn:
        conn.execute("DELETE FROM offers")
        conn.executemany(
            """
            INSERT INTO offers (id, title, description, offer_price, original_price,
                                discount_msg, valid_from, valid_until, product_ids, fetched_at)
            VALUES (:id, :title, :description, :offer_price, :original_price,
                    :discount_msg, :valid_from, :valid_until, :product_ids, :fetched_at)
            """,
            [{"product_ids": "", **o, "fetched_at": now} for o in offers],
        )


def upsert_coupons(coupons: list[dict]) -> None:
    """Replace all coupon rows with the latest sync results (DELETE then INSERT)."""
    if not coupons:
        return
    now = datetime.now(UTC).isoformat()
    with _conn() as conn:
        conn.execute("DELETE FROM coupons")
        conn.executemany(
            """
            INSERT INTO coupons (id, promotion_id, title, discount_title, discount_desc,
                                 valid_from, valid_until, is_activated, article_ids, fetched_at)
            VALUES (:id, :promotion_id, :title, :discount_title, :discount_desc,
                    :valid_from, :valid_until, :is_activated, :article_ids, :fetched_at)
            """,
            [{"article_ids": "", **c, "fetched_at": now} for c in coupons],
        )


def set_coupon_activated(promotion_id: str, activated: bool) -> None:
    """Update the is_activated flag for all coupons with the given promotion_id."""
    with _conn() as conn:
        conn.execute(
            "UPDATE coupons SET is_activated = ? WHERE promotion_id = ?",
            (1 if activated else 0, promotion_id),
        )


def coupon_id_for_promotion(promotion_id: str) -> str | None:
    """Return the coupon's own `id` for a given promotion_id, or None if unknown.

    The Lidl activation endpoint keys on the coupon `id`, not the `promotionId`
    the UI uses everywhere else.
    """
    with _conn() as conn:
        row = conn.execute(
            "SELECT id FROM coupons WHERE promotion_id = ? AND id != '' LIMIT 1",
            (promotion_id,),
        ).fetchone()
    return row["id"] if row else None


def receipt_ids() -> set[str]:
    """Return the set of all stored receipt IDs."""
    with _conn() as conn:
        rows = conn.execute("SELECT id FROM receipts").fetchall()
    return {row["id"] for row in rows}


def receipt_ids_with_items() -> set[str]:
    """Return the set of receipt IDs that have at least one item row."""
    with _conn() as conn:
        rows = conn.execute("SELECT DISTINCT receipt_id FROM items").fetchall()
    return {row["receipt_id"] for row in rows}


# ── read (used by dashboard) ─────────────────────────────────────────────────

def _where(days: int | None) -> tuple[str, list[Any]]:
    """Return (WHERE clause, params list) for receipt-level queries filtered by days."""
    if days is None:
        return "", []
    return "WHERE date >= date('now', ?)", [f"-{days} days"]


def _join_where(days: int | None) -> tuple[str, list[Any]]:
    """Same but using table alias r (for item JOIN queries)."""
    if days is None:
        return "", []
    return "WHERE r.date >= date('now', ?)", [f"-{days} days"]


def spending_by_day(days: int | None = None) -> list[dict]:
    """Return daily spend totals as [{date, total}] ordered by date."""
    where, params = _where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT date, SUM(total) AS total FROM receipts {where} GROUP BY date ORDER BY date",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def receipt_count(days: int | None = None) -> int:
    """Return the total number of receipts in the given period."""
    where, params = _where(days)
    with _conn() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM receipts {where}", params).fetchone()[0])


def total_spent(days: int | None = None) -> float:
    """Return sum of all receipt totals in the given period (0.0 if no data)."""
    where, params = _where(days)
    with _conn() as conn:
        row = conn.execute(
            f"SELECT COALESCE(SUM(total),0) FROM receipts {where}", params
        ).fetchone()
    return float(row[0])


def avg_per_visit(days: int | None = None) -> float:
    """Return average receipt total across all visits in the given period."""
    where, params = _where(days)
    with _conn() as conn:
        row = conn.execute(
            f"SELECT COALESCE(AVG(total),0) FROM receipts {where}", params
        ).fetchone()
    return float(row[0])


def biggest_purchase(days: int | None = None) -> float:
    """Return the highest single receipt total in the given period."""
    where, params = _where(days)
    with _conn() as conn:
        row = conn.execute(
            f"SELECT COALESCE(MAX(total),0) FROM receipts {where}", params
        ).fetchone()
    return float(row[0])


def total_discounts(days: int | None = None) -> float:
    """Return sum of all item discounts in the given period."""
    where, params = _join_where(days)
    with _conn() as conn:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(i.discount), 0)
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where}
            """,
            params,
        ).fetchone()
    return float(row[0])


def purchased_article_ids(days: int | None = None) -> dict[str, dict[str, Any]]:
    """
    Map each purchased article ID → {name, frequency} for matching against
    offer product_ids / coupon article_ids. Picks the most-common name per ID.
    """
    where, params = _join_where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT i.art_id, i.name, COUNT(*) AS frequency
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {("WHERE" if not where else where + " AND")} i.art_id != ''
            GROUP BY i.art_id, i.name
            """,
            params,
        ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        aid = row["art_id"]
        entry = result.setdefault(aid, {"name": row["name"], "frequency": 0})
        entry["frequency"] += row["frequency"]
        # prefer the name with the highest single-name count as the display name
        if row["frequency"] > entry.get("_top", 0):
            entry["name"] = row["name"]
            entry["_top"] = row["frequency"]
    for entry in result.values():
        entry.pop("_top", None)
    return result


def top_items_by_frequency(limit: int = 15, days: int | None = None) -> list[dict]:
    """Return items sorted by purchase count: [{name, frequency, total_spent}]."""
    where, params = _join_where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT i.name, COUNT(*) AS frequency,
                   SUM(i.price * i.quantity - i.discount) AS total_spent
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where}
            GROUP BY i.name
            ORDER BY frequency DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def top_items_by_spend(limit: int = 15, days: int | None = None) -> list[dict]:
    """Return items sorted by total spend: [{name, total_spent, frequency}]."""
    where, params = _join_where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT i.name, SUM(i.price * i.quantity - i.discount) AS total_spent,
                   COUNT(*) AS frequency
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where}
            GROUP BY i.name
            ORDER BY total_spent DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def item_avg_prices(days: int | None = None) -> list[dict]:
    """Lightweight per-item frequency + average price for ALL items (no limit)."""
    where, params = _join_where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT i.name, COUNT(*) AS frequency, ROUND(AVG(i.price), 2) AS avg_price
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where}
            GROUP BY i.name
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def item_price_stats(days: int | None = None, limit: int = 50) -> list[dict]:
    """
    Per-item price stats for the most-bought items: frequency, average, lowest,
    highest, and the price paid on the most recent purchase date.
    Groups by art_id when available so different products with the same name are not merged.
    """
    where, params = _join_where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(i.art_id,''), i.name) AS item_key,
                   MIN(i.name) AS name, i.art_id,
                   COUNT(*)        AS frequency,
                   ROUND(AVG(i.price), 2) AS avg_price,
                   ROUND(MIN(i.price), 2) AS min_price,
                   ROUND(MAX(i.price), 2) AS max_price,
                   ROUND((
                       SELECT i2.price FROM items i2
                       JOIN receipts r2 ON r2.id = i2.receipt_id
                       WHERE COALESCE(NULLIF(i2.art_id,''), i2.name) =
                             COALESCE(NULLIF(i.art_id,''), i.name)
                       ORDER BY r2.date DESC, i2.id DESC LIMIT 1
                   ), 2) AS last_price
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where}
            GROUP BY item_key
            ORDER BY frequency DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def monthly_spend(days: int | None = None) -> list[dict]:
    """Return monthly totals and visit counts: [{month, total, visits}]."""
    where, params = _where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT strftime('%Y-%m', date) AS month, SUM(total) AS total,
                   COUNT(*) AS visits
            FROM receipts {where}
            GROUP BY month ORDER BY month
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def visits_by_weekday(days: int | None = None) -> list[dict]:
    """Return visit count by day-of-week: [{day, visits}] for Mon–Sun, with zeros filled in."""
    where, params = _where(days)
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT CAST((strftime('%w', date) + 6) % 7 AS INTEGER) AS dow,
                   COUNT(*) AS visits
            FROM receipts {where}
            GROUP BY dow ORDER BY dow
            """,
            params,
        ).fetchall()
    counts = {r["dow"]: r["visits"] for r in rows}
    return [{"day": labels[i], "visits": counts.get(i, 0)} for i in range(7)]


def price_trends(days: int | None = None, min_dates: int = 3) -> list[dict]:
    """
    For each item bought on at least min_dates distinct dates, return the
    % price change between first and last purchase. Sorted DESC (biggest rise first).

    Groups by (name, art_id) so that two products with the same display name but
    different article IDs (e.g. different pack sizes) are not merged together.
    Rows with an empty art_id fall back to name-only grouping.
    """
    where, params = _join_where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            WITH dated AS (
                SELECT i.name, COALESCE(NULLIF(i.art_id,''), i.name) AS item_key,
                       r.date, AVG(i.price) AS price
                FROM items i
                JOIN receipts r ON r.id = i.receipt_id
                {where}
                GROUP BY item_key, r.date
            ),
            bounds AS (
                SELECT item_key, MIN(name) AS name,
                       MIN(date) AS first_date,
                       MAX(date) AS last_date,
                       COUNT(*)  AS num_dates
                FROM dated
                GROUP BY item_key
                HAVING num_dates >= ?
            )
            SELECT b.name, b.num_dates,
                   ROUND(d1.price, 2) AS first_price,
                   ROUND(d2.price, 2) AS last_price,
                   ROUND((d2.price - d1.price) / d1.price * 100, 1) AS pct_change
            FROM bounds b
            JOIN dated d1 ON d1.item_key = b.item_key AND d1.date = b.first_date
            JOIN dated d2 ON d2.item_key = b.item_key AND d2.date = b.last_date
            WHERE d1.price > 0 AND pct_change != 0
            ORDER BY pct_change DESC
            """,
            (*params, min_dates),
        ).fetchall()
    return [dict(r) for r in rows]


def price_history_for_item(item_key: str, days: int | None = None) -> list[dict]:
    """
    Return average unit price per purchase date for a specific item: [{date, price}].
    item_key should be the art_id when non-empty, otherwise the item name.
    """
    where, params = _join_where(days)
    and_clause = "AND" if where else ""
    join_where_stripped = where.replace("WHERE ", "") if where else ""
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT r.date, AVG(i.price) AS price
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            WHERE COALESCE(NULLIF(i.art_id,''), i.name) = ?
            {and_clause} {join_where_stripped}
            GROUP BY r.date ORDER BY r.date
            """,
            (item_key, *params),
        ).fetchall()
    return [dict(r) for r in rows]


def staple_items(days: int | None = None, min_pct: float = 20.0) -> list[dict]:
    """Return items that appear in at least min_pct% of trips: [{name, visits, pct, total_spent}]."""
    where, params = _join_where(days)
    total = receipt_count(days)
    if not total:
        return []
    min_count = max(2, int(total * min_pct / 100))
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT i.name,
                   COUNT(DISTINCT r.id) AS visits,
                   ROUND(COUNT(DISTINCT r.id) * 100.0 / ?, 1) AS pct,
                   ROUND(SUM(i.price * i.quantity - i.discount), 2) AS total_spent
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where}
            GROUP BY i.name
            HAVING visits >= ?
            ORDER BY visits DESC
            LIMIT 30
            """,
            (total, *params, min_count),
        ).fetchall()
    return [dict(r) for r in rows]


def receipts_for_date(date: str) -> list[dict]:
    """Return all receipts for a specific ISO date string (YYYY-MM-DD)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM receipts WHERE date = ? ORDER BY store_name", (date,)
        ).fetchall()
    return [dict(r) for r in rows]


def items_for_receipt(receipt_id: str) -> list[dict]:
    """Return all items for a receipt ordered by name."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT name, quantity, price, discount FROM items WHERE receipt_id = ? ORDER BY name",
            (receipt_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def all_offers() -> list[dict]:
    """Return all stored offers ordered by title."""
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM offers ORDER BY title").fetchall()
    return [dict(r) for r in rows]


def current_offers() -> list[dict]:
    """Return offers that are currently valid (valid_from ≤ today ≤ valid_until)."""
    today = datetime.now(UTC).date().isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM offers WHERE (valid_from IS NULL OR valid_from <= ?) "
            "AND (valid_until IS NULL OR valid_until >= ?) ORDER BY title",
            (today, today),
        ).fetchall()
    return [dict(r) for r in rows]


def upcoming_offers() -> list[dict]:
    """Return offers that start after today, ordered by start date."""
    today = datetime.now(UTC).date().isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM offers WHERE valid_from > ? ORDER BY valid_from, title",
            (today,),
        ).fetchall()
    return [dict(r) for r in rows]


def all_coupons() -> list[dict]:
    """Return all stored coupons ordered by title."""
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM coupons ORDER BY title").fetchall()
    return [dict(r) for r in rows]


def current_coupons() -> list[dict]:
    """Return coupons that are currently valid."""
    today = datetime.now(UTC).date().isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM coupons WHERE (valid_from IS NULL OR valid_from <= ?) "
            "AND (valid_until IS NULL OR valid_until >= ?) ORDER BY title",
            (today, today),
        ).fetchall()
    return [dict(r) for r in rows]


def upcoming_coupons() -> list[dict]:
    """Return coupons that start after today, ordered by start date."""
    today = datetime.now(UTC).date().isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM coupons WHERE valid_from > ? ORDER BY valid_from, title",
            (today,),
        ).fetchall()
    return [dict(r) for r in rows]


def matched_offers(top_n: int = 50) -> list[dict]:
    """
    Return offers whose title contains words from the user's most-bought items.
    Simple keyword overlap — good enough without a dependency on fuzzy matching.
    """
    top = top_items_by_frequency(limit=top_n)
    if not top:
        return []

    # Build a set of lowercased words from item names (≥4 chars to avoid noise)
    item_words: set[str] = set()
    for row in top:
        for word in row["name"].lower().split():
            if len(word) >= 4:
                item_words.add(word)

    offers = all_offers()
    matches = []
    for offer in offers:
        title_words = set(offer["title"].lower().split())
        if item_words & title_words:
            matches.append(offer)
    return matches


def export_items(days: int | None = None) -> list[dict]:
    """Return all receipt items joined with date and store for CSV export."""
    where, params = _join_where(days)
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT r.date, r.store_name, r.currency,
                   i.name, i.quantity, i.price, i.discount,
                   ROUND(i.quantity * i.price - i.discount, 2) AS net_total
            FROM items i
            JOIN receipts r ON r.id = i.receipt_id
            {where}
            ORDER BY r.date, r.store_name, i.name
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def spending_summary_text(days: int | None = None, currency: str = "EUR") -> str:
    """Plain-text spending summary for the AI prompt."""
    from datetime import date as _date

    spent = total_spent(days)
    visits = receipt_count(days)
    top = top_items_by_frequency(limit=10, days=days)
    by_day = spending_by_day(days)

    period = f"last {days} days" if days else "all time"
    lines = [
        f"Period: {period}. Total: {currency} {spent:.2f} across {visits} visits.",
        "",
        "Top 10 most-bought items:",
    ]
    for item in top:
        lines.append(
            f"  - {item['name']}: bought {item['frequency']}x, "
            f"total {currency} {item['total_spent']:.2f}"
        )

    if by_day:
        weekly: dict[str, float] = {}
        for row in by_day:
            d = _date.fromisoformat(row["date"])
            week = d.strftime("%Y-W%W")
            weekly[week] = weekly.get(week, 0) + row["total"]
        lines.append("\nWeekly spend (most recent 8 weeks):")
        for week, total in sorted(weekly.items())[-8:]:
            lines.append(f"  {week}: {currency} {total:.2f}")

    return "\n".join(lines)
