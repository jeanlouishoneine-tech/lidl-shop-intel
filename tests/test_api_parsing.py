"""Tests for api.py — receipt parsing (JSON and HTML formats)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import api  # noqa: E402

# ── JSON format ───────────────────────────────────────────────────────────────

def test_parse_receipt_json_basic():
    raw = {
        "id": "ticket_1",
        "date": "2025-03-15T10:00:00Z",
        "store": {"id": "s1", "name": "Lidl City"},
        "totalAmount": {"amount": 12.50, "currency": "EUR"},
        "itemsLines": [
            {"description": "Milk", "quantity": 2, "currentUnitPrice": 1.0, "codeInput": "4001"},
            {"description": "Bread", "quantity": 1, "currentUnitPrice": 2.5, "codeInput": "5001"},
        ],
    }
    receipt_row, item_rows = api.parse_receipt(raw)
    assert receipt_row["id"] == "ticket_1"
    assert receipt_row["date"] == "2025-03-15"
    assert receipt_row["total"] == pytest.approx(12.50)
    assert receipt_row["currency"] == "EUR"
    assert receipt_row["store_name"] == "Lidl City"
    assert len(item_rows) == 2
    assert item_rows[0]["name"] == "Milk"
    assert item_rows[1]["name"] == "Bread"


def test_parse_receipt_json_currency_object():
    raw = {
        "id": "t1",
        "date": "2025-01-01",
        "totalAmount": {"amount": 9.99, "currency": "CHF"},
        "itemsLines": [],
    }
    receipt_row, _ = api.parse_receipt(raw)
    assert receipt_row["currency"] == "CHF"
    assert receipt_row["total"] == pytest.approx(9.99)


def test_parse_receipt_json_currency_flat():
    raw = {
        "id": "t1",
        "date": "2025-01-01",
        "total": 15.00,
        "currency": {"code": "GBP"},
        "itemsLines": [],
    }
    receipt_row, _ = api.parse_receipt(raw)
    assert receipt_row["currency"] == "GBP"
    assert receipt_row["total"] == pytest.approx(15.0)


def test_parse_receipt_json_empty_dict():
    receipt_row, item_rows = api.parse_receipt({})
    assert receipt_row["id"] == ""
    assert receipt_row["total"] == 0.0
    assert item_rows == []


def test_parse_receipt_json_art_id_from_code_input():
    raw = {
        "id": "t1",
        "date": "2025-01-01",
        "itemsLines": [{"description": "Butter", "quantity": 1, "currentUnitPrice": 3.0, "codeInput": "9999"}],
    }
    _, item_rows = api.parse_receipt(raw)
    assert item_rows[0]["art_id"] == "9999"


def test_parse_receipt_item_ids_generated():
    raw = {
        "id": "rx",
        "date": "2025-01-01",
        "itemsLines": [
            {"description": "A", "quantity": 1, "currentUnitPrice": 1.0},
            {"description": "B", "quantity": 1, "currentUnitPrice": 2.0},
        ],
    }
    _, item_rows = api.parse_receipt(raw)
    assert item_rows[0]["id"] == "rx_0"
    assert item_rows[1]["id"] == "rx_1"


# ── HTML format ───────────────────────────────────────────────────────────────

def _html_receipt(html_body: str) -> dict:
    return {"id": "html1", "date": "2025-04-01", "ticketType": "HTML", "htmlPrintedReceipt": html_body}


def test_parse_receipt_html_basic():
    html = (
        '<span class="article" data-art-description="Milk" data-art-quantity="1" data-unit-price="1.50" data-art-id="111"></span>'
        '<span class="article" data-art-description="Eggs" data-art-quantity="1" data-unit-price="2.99" data-art-id="222"></span>'
    )
    _, item_rows = api.parse_receipt(_html_receipt(html))
    names = [r["name"] for r in item_rows]
    assert "Milk" in names
    assert "Eggs" in names
    assert len(item_rows) == 2


def test_parse_receipt_html_discount_span():
    html = (
        '<span class="article" data-art-description="Juice" data-art-quantity="1" data-unit-price="3.00" data-art-id=""></span>'
        '<span class="discount">-1.50</span>'
    )
    _, item_rows = api.parse_receipt(_html_receipt(html))
    assert len(item_rows) == 1
    assert item_rows[0]["discount"] == pytest.approx(1.50)


def test_parse_receipt_html_breakdown_count():
    """Breakdown span '2 x 3.99' should update quantity and not create a new item."""
    html = (
        '<span class="article" data-art-description="Milk" data-art-quantity="1" data-unit-price="3.99" data-art-id=""></span>'
        '<span class="article" data-art-description="Milk" data-art-quantity="1" data-unit-price="3.99" data-art-id="">2 x 3.99</span>'
    )
    _, item_rows = api.parse_receipt(_html_receipt(html))
    milk_rows = [r for r in item_rows if r["name"] == "Milk"]
    assert len(milk_rows) == 1
    assert milk_rows[0]["quantity"] == pytest.approx(2.0)


def test_parse_receipt_html_breakdown_weight():
    """Weight breakdown '1.076 kg x 13.90 CHF/kg' should set quantity to 1.076."""
    html = (
        '<span class="article" data-art-description="Beef" data-art-quantity="1" data-unit-price="13.90" data-art-id=""></span>'
        '<span class="article" data-art-description="Beef" data-art-quantity="1" data-unit-price="13.90" data-art-id="">1.076 kg x 13.90 CHF/kg</span>'
    )
    _, item_rows = api.parse_receipt(_html_receipt(html))
    beef_rows = [r for r in item_rows if r["name"] == "Beef"]
    assert len(beef_rows) == 1
    assert beef_rows[0]["quantity"] == pytest.approx(1.076)


def test_parse_receipt_html_empty_string():
    _, item_rows = api.parse_receipt(_html_receipt(""))
    assert item_rows == []


def test_parse_receipt_html_no_matching_spans():
    _, item_rows = api.parse_receipt(_html_receipt("<div>nothing here</div>"))
    assert item_rows == []


def test_parse_receipt_html_entities():
    html = '<span class="article" data-art-description="Riz &amp; Quinoa" data-art-quantity="1" data-unit-price="2.00" data-art-id=""></span>'
    _, item_rows = api.parse_receipt(_html_receipt(html))
    assert item_rows[0]["name"] == "Riz & Quinoa"


# ── regex helpers ─────────────────────────────────────────────────────────────

def test_breakdown_re_matches_count():
    assert api._BREAKDOWN_RE.match("  2 x 6.49  ")


def test_breakdown_re_matches_weight():
    assert api._BREAKDOWN_RE.match("1.076 kg x 13.90  CHF/kg")


def test_breakdown_re_no_match():
    assert not api._BREAKDOWN_RE.match("Chicken 4.99")
