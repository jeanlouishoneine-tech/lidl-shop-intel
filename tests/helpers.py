"""Shared test helper functions."""
import sqlite3


def insert_receipt(
    conn: sqlite3.Connection,
    id: str,
    date: str,
    total: float = 10.0,
    store_name: str = "Lidl",
    currency: str = "EUR",
) -> None:
    """Insert a minimal receipt row directly (bypasses upsert_receipt)."""
    conn.execute(
        "INSERT INTO receipts (id, date, store_name, total, currency) VALUES (?,?,?,?,?)",
        (id, date, store_name, total, currency),
    )
    conn.commit()


def insert_item(
    conn: sqlite3.Connection,
    id: str,
    receipt_id: str,
    name: str,
    quantity: float = 1.0,
    price: float = 1.0,
    discount: float = 0.0,
    art_id: str = "",
) -> None:
    """Insert a minimal item row directly."""
    conn.execute(
        "INSERT INTO items (id, receipt_id, name, quantity, price, discount, art_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (id, receipt_id, name, quantity, price, discount, art_id),
    )
    conn.commit()
