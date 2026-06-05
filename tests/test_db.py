"""Tests for db.py — schema, write operations, and analytics queries."""
import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "src"))
sys.path.insert(0, str(_HERE))

from helpers import insert_item, insert_receipt  # noqa: E402

import db  # noqa: E402

# ── schema ────────────────────────────────────────────────────────────────────

def test_init_db_creates_tables(mem_db):
    conn = sqlite3.connect(mem_db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"receipts", "items", "offers", "coupons"}.issubset(tables)
    conn.close()


def test_init_db_is_idempotent(mem_db):
    db.init_db()  # second call should not raise
    conn = sqlite3.connect(mem_db)
    count = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    conn.close()
    assert count == 0


# ── write: upsert_receipt ─────────────────────────────────────────────────────

def test_upsert_receipt_inserts(mem_db):
    db.upsert_receipt({"id": "r1", "date": "2025-01-01", "store_name": "Lidl", "store_id": "s1",
                       "total": 25.0, "currency": "EUR"})
    conn = sqlite3.connect(mem_db)
    row = conn.execute("SELECT * FROM receipts WHERE id='r1'").fetchone()
    conn.close()
    assert row is not None
    assert row[1] == "2025-01-01"
    assert row[4] == 25.0


def test_upsert_receipt_updates_on_conflict(mem_db):
    db.upsert_receipt({"id": "r1", "date": "2025-01-01", "store_name": "OldName",
                       "store_id": "s1", "total": 10.0, "currency": "EUR"})
    db.upsert_receipt({"id": "r1", "date": "2025-01-01", "store_name": "NewName",
                       "store_id": "s1", "total": 10.0, "currency": "EUR"})
    conn = sqlite3.connect(mem_db)
    name = conn.execute("SELECT store_name FROM receipts WHERE id='r1'").fetchone()[0]
    conn.close()
    assert name == "NewName"


# ── write: upsert_items ───────────────────────────────────────────────────────

def test_upsert_items_empty_list(mem_db):
    db.upsert_items([])  # should not raise


def test_upsert_items_inserts(mem_db):
    db.upsert_receipt({"id": "r1", "date": "2025-01-01", "store_name": "Lidl",
                       "store_id": "", "total": 5.0, "currency": "EUR"})
    db.upsert_items([
        {"id": "r1_0", "receipt_id": "r1", "name": "Milk", "quantity": 2.0, "price": 1.0,
         "discount": 0.0, "art_id": ""},
        {"id": "r1_1", "receipt_id": "r1", "name": "Bread", "quantity": 1.0, "price": 2.5,
         "discount": 0.0, "art_id": ""},
    ])
    conn = sqlite3.connect(mem_db)
    count = conn.execute("SELECT COUNT(*) FROM items WHERE receipt_id='r1'").fetchone()[0]
    conn.close()
    assert count == 2


def test_upsert_items_no_duplicate_on_conflict(mem_db):
    db.upsert_receipt({"id": "r1", "date": "2025-01-01", "store_name": "Lidl",
                       "store_id": "", "total": 2.0, "currency": "EUR"})
    item = {"id": "r1_0", "receipt_id": "r1", "name": "Milk", "quantity": 1.0,
            "price": 1.0, "discount": 0.0, "art_id": ""}
    db.upsert_items([item])
    db.upsert_items([item])  # second insert should be silently ignored
    conn = sqlite3.connect(mem_db)
    count = conn.execute("SELECT COUNT(*) FROM items WHERE id='r1_0'").fetchone()[0]
    conn.close()
    assert count == 1


# ── write: upsert_offers ──────────────────────────────────────────────────────

def test_upsert_offers_replaces_all(mem_db):
    offer = {"id": "o1", "title": "Bananas", "description": "", "offer_price": 1.0,
              "original_price": 2.0, "discount_msg": "-50%", "valid_from": "2025-01-01",
              "valid_until": "2025-01-31", "product_ids": "123"}
    db.upsert_offers([offer])
    db.upsert_offers([{**offer, "id": "o2", "title": "Apples"}])
    conn = sqlite3.connect(mem_db)
    titles = {row[0] for row in conn.execute("SELECT title FROM offers").fetchall()}
    conn.close()
    assert "Apples" in titles
    assert "Bananas" not in titles


# ── write: set_coupon_activated ───────────────────────────────────────────────

def test_set_coupon_activated_toggle(mem_db):
    coupon = {"id": "c1", "promotion_id": "p1", "title": "10% Off",
              "discount_title": "-10%", "discount_desc": "", "valid_from": "2025-01-01",
              "valid_until": "2025-01-31", "is_activated": 0, "article_ids": ""}
    db.upsert_coupons([coupon])
    db.set_coupon_activated("p1", True)
    conn = sqlite3.connect(mem_db)
    val = conn.execute("SELECT is_activated FROM coupons WHERE promotion_id='p1'").fetchone()[0]
    conn.close()
    assert val == 1

    db.set_coupon_activated("p1", False)
    conn = sqlite3.connect(mem_db)
    val = conn.execute("SELECT is_activated FROM coupons WHERE promotion_id='p1'").fetchone()[0]
    conn.close()
    assert val == 0


# ── analytics: empty DB ───────────────────────────────────────────────────────

def test_receipt_count_empty(mem_db):
    assert db.receipt_count() == 0


def test_total_spent_empty(mem_db):
    assert db.total_spent() == 0.0


def test_avg_per_visit_empty(mem_db):
    assert db.avg_per_visit() == 0.0


def test_biggest_purchase_empty(mem_db):
    assert db.biggest_purchase() == 0.0


def test_total_discounts_empty(mem_db):
    assert db.total_discounts() == 0.0


# ── analytics: known data ─────────────────────────────────────────────────────

def test_total_spent(mem_db):
    conn = sqlite3.connect(mem_db)
    insert_receipt(conn, "r1", "2025-01-01", total=10.0)
    insert_receipt(conn, "r2", "2025-01-02", total=20.0)
    insert_receipt(conn, "r3", "2025-01-03", total=30.0)
    conn.close()
    assert db.total_spent() == pytest.approx(60.0)


def test_avg_per_visit(mem_db):
    conn = sqlite3.connect(mem_db)
    insert_receipt(conn, "r1", "2025-01-01", total=10.0)
    insert_receipt(conn, "r2", "2025-01-02", total=20.0)
    insert_receipt(conn, "r3", "2025-01-03", total=30.0)
    conn.close()
    assert db.avg_per_visit() == pytest.approx(20.0)


def test_biggest_purchase(mem_db):
    conn = sqlite3.connect(mem_db)
    insert_receipt(conn, "r1", "2025-01-01", total=10.0)
    insert_receipt(conn, "r2", "2025-01-02", total=99.0)
    conn.close()
    assert db.biggest_purchase() == pytest.approx(99.0)


def test_total_discounts(mem_db):
    conn = sqlite3.connect(mem_db)
    insert_receipt(conn, "r1", "2025-01-01", total=20.0)
    insert_item(conn, "i1", "r1", "Milk", discount=1.50)
    insert_item(conn, "i2", "r1", "Bread", discount=0.50)
    conn.close()
    assert db.total_discounts() == pytest.approx(2.0)


# ── analytics: date filtering ─────────────────────────────────────────────────

def test_total_spent_days_filter(mem_db):
    conn = sqlite3.connect(mem_db)
    insert_receipt(conn, "old", "2020-01-01", total=100.0)
    insert_receipt(conn, "new", "2025-05-30", total=20.0)
    conn.close()
    # Only the recent receipt should be within 30 days of "now" (2025-xx-xx)
    # Use a wide window so this test is not date-sensitive
    recent = db.total_spent(days=365 * 10)  # 10-year window catches both
    assert recent == pytest.approx(120.0)
    old_filtered = db.total_spent(days=30)
    # The 2020 receipt is definitely >30 days old; the 2025 one may or may not be
    # depending on the actual date — we just assert the 2020 one is excluded
    assert old_filtered < 120.0


# ── analytics: spending_by_day grouping ──────────────────────────────────────

def test_spending_by_day_groups_same_date(mem_db):
    conn = sqlite3.connect(mem_db)
    insert_receipt(conn, "r1", "2025-03-01", total=10.0)
    insert_receipt(conn, "r2", "2025-03-01", total=15.0)
    conn.close()
    rows = db.spending_by_day()
    assert len(rows) == 1
    assert rows[0]["total"] == pytest.approx(25.0)


# ── analytics: top items ─────────────────────────────────────────────────────

def test_top_items_by_frequency_order(mem_db):
    conn = sqlite3.connect(mem_db)
    for i in range(5):
        rid = f"r{i}"
        insert_receipt(conn, rid, f"2025-01-0{i + 1}", total=10.0)
        insert_item(conn, f"{rid}_milk", rid, "Milk", price=1.0)
    insert_receipt(conn, "rx", "2025-01-10", total=5.0)
    insert_item(conn, "rx_bread", "rx", "Bread", price=2.0)
    conn.close()
    top = db.top_items_by_frequency(limit=5)
    assert top[0]["name"] == "Milk"
    assert top[0]["frequency"] == 5


def test_top_items_by_spend_order(mem_db):
    conn = sqlite3.connect(mem_db)
    insert_receipt(conn, "r1", "2025-01-01", total=10.0)
    insert_item(conn, "i1", "r1", "Cheap", price=1.0)
    insert_item(conn, "i2", "r1", "Expensive", price=9.0)
    conn.close()
    top = db.top_items_by_spend(limit=5)
    assert top[0]["name"] == "Expensive"


# ── analytics: visits_by_weekday ─────────────────────────────────────────────

def test_visits_by_weekday_fills_zeros(mem_db):
    conn = sqlite3.connect(mem_db)
    # 2025-01-06 is a Monday
    insert_receipt(conn, "r1", "2025-01-06", total=10.0)
    conn.close()
    rows = db.visits_by_weekday()
    assert len(rows) == 7
    monday = next(r for r in rows if r["day"] == "Mon")
    assert monday["visits"] == 1
    other_days = [r for r in rows if r["day"] != "Mon"]
    assert all(r["visits"] == 0 for r in other_days)


# ── analytics: price_trends ───────────────────────────────────────────────────

def test_price_trends_requires_min_dates(mem_db):
    conn = sqlite3.connect(mem_db)
    for i in range(2):
        rid = f"r{i}"
        insert_receipt(conn, rid, f"2025-01-0{i + 1}", total=5.0)
        insert_item(conn, f"{rid}_m", rid, "Milk", price=1.0 + i * 0.5)
    conn.close()
    # With min_dates=3 and only 2 purchase dates, Milk should not appear
    trends = db.price_trends(min_dates=3)
    assert all(t["name"] != "Milk" for t in trends)


def test_price_trends_appears_with_enough_dates(mem_db):
    conn = sqlite3.connect(mem_db)
    prices = [1.0, 1.5, 2.0]
    for i, p in enumerate(prices):
        rid = f"r{i}"
        insert_receipt(conn, rid, f"2025-01-0{i + 1}", total=p)
        insert_item(conn, f"{rid}_m", rid, "Milk", price=p)
    conn.close()
    trends = db.price_trends(min_dates=3)
    milk = next((t for t in trends if t["name"] == "Milk"), None)
    assert milk is not None
    assert milk["pct_change"] > 0


# ── analytics: staple_items ───────────────────────────────────────────────────

def test_staple_items_threshold(mem_db):
    conn = sqlite3.connect(mem_db)
    for i in range(10):
        rid = f"r{i}"
        insert_receipt(conn, rid, f"2025-01-{i + 1:02d}", total=10.0)
        if i < 2:  # Milk in 2 out of 10 = 20%
            insert_item(conn, f"{rid}_milk", rid, "Milk")
    conn.close()
    staples = db.staple_items(min_pct=20.0)
    names = [s["name"] for s in staples]
    assert "Milk" in names


def test_staple_items_below_threshold(mem_db):
    conn = sqlite3.connect(mem_db)
    for i in range(10):
        rid = f"r{i}"
        insert_receipt(conn, rid, f"2025-01-{i + 1:02d}", total=10.0)
        if i == 0:  # Milk in 1 out of 10 = 10%
            insert_item(conn, f"{rid}_milk", rid, "Milk")
    conn.close()
    staples = db.staple_items(min_pct=20.0)
    names = [s["name"] for s in staples]
    assert "Milk" not in names


# ── offers: date filtering ────────────────────────────────────────────────────

def test_current_offers_date_filter(mem_db):
    db.upsert_offers([
        {"id": "active", "title": "Active", "description": "", "offer_price": 1.0,
         "original_price": 2.0, "discount_msg": "-50%",
         "valid_from": "2020-01-01", "valid_until": "2099-12-31", "product_ids": ""},
        {"id": "expired", "title": "Expired", "description": "", "offer_price": 1.0,
         "original_price": 2.0, "discount_msg": "-50%",
         "valid_from": "2020-01-01", "valid_until": "2020-01-02", "product_ids": ""},
    ])
    current = db.current_offers()
    titles = [o["title"] for o in current]
    assert "Active" in titles
    assert "Expired" not in titles


def test_upcoming_offers(mem_db):
    db.upsert_offers([
        {"id": "future", "title": "Future", "description": "", "offer_price": 1.0,
         "original_price": 2.0, "discount_msg": "-50%",
         "valid_from": "2099-01-01", "valid_until": "2099-12-31", "product_ids": ""},
    ])
    upcoming = db.upcoming_offers()
    assert any(o["title"] == "Future" for o in upcoming)
    current = db.current_offers()
    assert not any(o["title"] == "Future" for o in current)
