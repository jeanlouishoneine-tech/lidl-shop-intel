"""
Thin wrapper around lidlplus-api. Loads credentials from .env and exposes
clean methods for the rest of the app to use.
"""
import html as _html
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# Matches Lidl's quantity breakdown lines in two formats:
#   count items : "2 x 6.49"
#   weight items: "1.076 kg x 13.90  CHF/kg"
_BREAKDOWN_RE = re.compile(
    r'^\s*[\d.,]+(\s+\w+)?\s+x\s+[\d.,]+(\s+CHF/\w+)?\s*$',
    re.IGNORECASE,
)

from dotenv import load_dotenv
from lidlplus_api import LidlPlusApi

load_dotenv(Path(__file__).parent.parent / ".env")


def _client() -> LidlPlusApi:
    token = os.getenv("LIDL_REFRESH_TOKEN", "")
    if not token:
        sys.exit(
            "No LIDL_REFRESH_TOKEN found.\n"
            "Run:  uv run python src/auth.py"
        )
    language = os.getenv("LIDL_LANGUAGE", "fr")
    country = os.getenv("LIDL_COUNTRY", "CH")
    return LidlPlusApi(language, country, refresh_token=token)


# ── receipts ────────────────────────────────────────────────────────────────

def fetch_receipt_headers(max_pages: int = 20) -> list[dict]:
    """Return all receipt summaries (paginated)."""
    api = _client()
    results = []
    for page in range(1, max_pages + 1):
        data = api.receipts(pageNumber=page)
        tickets = data.get("tickets", [])
        results.extend(tickets)
        total = data.get("totalCount", 0)
        if len(results) >= total:
            break
    return results


def fetch_receipt_detail(ticket_id: str) -> dict:
    """Return a single receipt with full line-item detail."""
    return _client().receipt(ticket_id)


# ── stores ───────────────────────────────────────────────────────────────────

def fetch_stores() -> list[dict]:
    """Return all Lidl stores (no auth required)."""
    return _client().get_stores()


# ── offers & coupons ─────────────────────────────────────────────────────────

def _store_id() -> str | None:
    return os.getenv("LIDL_STORE_ID") or None


def fetch_offers() -> list[dict]:
    """Return current store offers, normalised to a flat list of dicts."""
    sid = _store_id()
    if not sid:
        return []
    try:
        data = _client().offers(sid)
        raw = data if isinstance(data, list) else data.get("offers", data.get("items", []))
        out = []
        for o in raw:
            pb = o.get("priceBox") or {}
            out.append({
                "id":             o.get("id", ""),
                "title":          o.get("title") or o.get("name") or "",
                "description":    o.get("packaging") or "",
                "offer_price":    pb.get("largePartNumeric"),
                "original_price": pb.get("smallPartNumeric"),
                "discount_msg":   pb.get("discountMessage") or "",
                "valid_from":     (o.get("startValidityDate") or "")[:10],
                "valid_until":    (o.get("endValidityDate")   or "")[:10],
                "product_ids":    ",".join(o.get("productIds") or []),
            })
        return [o for o in out if o["title"]]
    except Exception:
        return []


def fetch_coupons() -> list[dict]:
    """Return available coupons, normalised. Uses 'promotions' key from the API."""
    sid = _store_id()
    if not sid:
        return []
    try:
        data = _client().coupons(sid)
        raw: list = []
        if isinstance(data, list):
            raw = data
        else:
            for section in data.get("sections", []):
                # API returns "promotions", older responses may use "coupons"
                raw.extend(section.get("promotions", section.get("coupons", [])))
        out = []
        for c in raw:
            validity  = c.get("validity") or {}
            discount  = c.get("discount") or {}
            out.append({
                "id":             c.get("id", ""),
                "promotion_id":   c.get("promotionId") or c.get("id", ""),
                "title":          c.get("title") or "",
                "discount_title": discount.get("title") or c.get("discountTitle") or "",
                "discount_desc":  discount.get("description") or "",
                "valid_from":     (validity.get("start") or "")[:10],
                "valid_until":    (validity.get("end")   or "")[:10],
                "is_activated":   1 if c.get("isActivated") else 0,
                "article_ids":    ",".join(c.get("articleIds") or []),
            })
        return [c for c in out if c["title"]]
    except Exception:
        return []


def activate_coupon(coupon_id: str) -> bool:
    return _client().activate_coupon(coupon_id)


def deactivate_coupon(coupon_id: str) -> bool:
    return _client().deactivate_coupon(coupon_id)


# ── receipt parsing ──────────────────────────────────────────────────────────

_DISCOUNT_AMOUNT_RE = re.compile(r'-([\d.,]+)\s*$')


class _ReceiptSpanCollector(HTMLParser):
    """
    Pass 1: collect receipt lines grouped by span id.

    Lidl's HTML splits a single item line across several <span> elements for
    CSS formatting — e.g. name in bold, spaces in regular, price in bold.
    All fragments share the same id="purchase_list_line_N", so grouping by id
    merges them back into one logical line with the full combined text.

    Class matching checks for word membership so "article css_bold" → "article".
    """

    def __init__(self):
        super().__init__()
        self._by_id: dict[str, dict] = {}
        self._order: list[str] = []
        self._fallback = 0
        self._active_key: str | None = None

    @staticmethod
    def _effective_cls(cls_str: str) -> str | None:
        words = cls_str.split()
        for w in ("article", "discount"):
            if w in words:
                return w
        return None

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            self._active_key = None
            return
        a = dict(attrs)
        cls = self._effective_cls(a.get("class", ""))
        if not cls:
            self._active_key = None
            return
        key = a.get("id") or f"_fb_{self._fallback}"
        if not a.get("id"):
            self._fallback += 1
        self._active_key = key
        if key not in self._by_id:
            self._by_id[key] = {"cls": cls, "attrs": a, "text": ""}
            self._order.append(key)

    def handle_data(self, data):
        if self._active_key and self._active_key in self._by_id:
            self._by_id[self._active_key]["text"] += data

    def handle_endtag(self, tag):
        if tag == "span":
            self._active_key = None

    @property
    def spans(self) -> list[tuple[str, dict, str]]:
        return [(self._by_id[k]["cls"], self._by_id[k]["attrs"], self._by_id[k]["text"])
                for k in self._order]


def _parse_html_items(html_str: str) -> list[dict]:
    """
    Two-pass approach:
      Pass 1 — collect all article + discount spans with their text.
      Pass 2 — process sequentially:
        • article span  : start new item (attr qty is used as default)
        • next span is a breakdown ("N x price") for the same item
              → extract actual quantity, skip that span
        • discount span : parse amount and add to the last item's discount
    """
    collector = _ReceiptSpanCollector()
    collector.feed(html_str)
    spans = collector.spans

    items: list[dict] = []
    i = 0
    while i < len(spans):
        cls, attrs, text = spans[i]

        if cls == "discount":
            m = _DISCOUNT_AMOUNT_RE.search(text.strip())
            if m and items:
                items[-1]["discount"] += float(m.group(1).replace(",", "."))
            i += 1
            continue

        # cls == "article"
        name = _html.unescape(attrs.get("data-art-description") or "").strip()
        if not name:
            i += 1
            continue

        qty    = float(attrs.get("data-art-quantity") or 1)
        price  = float(attrs.get("data-unit-price") or 0)
        art_id = (attrs.get("data-art-id") or "").strip()

        # Look ahead: if next span is a breakdown for the same item, use its qty
        if i + 1 < len(spans):
            n_cls, n_attrs, n_text = spans[i + 1]
            n_name = _html.unescape(n_attrs.get("data-art-description") or "").strip()
            if n_cls == "article" and n_name == name and _BREAKDOWN_RE.match(n_text):
                # extract the leading number from "1.076 kg x …" or "2 x …"
                m = re.search(r'[\d.,]+', n_text.split("x")[0])
                if m:
                    try:
                        qty = float(m.group().replace(",", "."))
                    except ValueError:
                        pass
                i += 2  # consume both main span and breakdown span
                items.append({"name": name, "quantity": qty, "price": price, "discount": 0.0, "art_id": art_id})
                continue

        items.append({"name": name, "quantity": qty, "price": price, "discount": 0.0, "art_id": art_id})
        i += 1

    return items


def parse_receipt(raw: dict) -> tuple[dict, list[dict]]:
    """
    Normalise a raw receipt detail into a (receipt_row, [item_rows]) pair.
    Handles both Lidl's HTML receipt format (ticketType=HTML) and the older JSON format.
    """
    receipt_id = raw.get("id", "")
    date = (raw.get("date") or raw.get("taxDate") or "")[:10]  # keep YYYY-MM-DD

    store = raw.get("store") or {}
    store_id = store.get("id", raw.get("storeCode", ""))
    store_name = store.get("name", store.get("shortName", ""))

    total_obj = raw.get("totalAmount") or raw.get("total") or 0
    if isinstance(total_obj, dict):
        total = float(total_obj.get("amount", 0))
        currency = total_obj.get("currency", "CHF")
    else:
        total = float(total_obj or 0)
        currency_obj = raw.get("currency") or {}
        currency = currency_obj.get("code", "CHF") if isinstance(currency_obj, dict) else str(currency_obj or "CHF")

    receipt_row = {
        "id": receipt_id,
        "date": date,
        "store_name": store_name,
        "store_id": store_id,
        "total": total,
        "currency": currency,
    }

    # Lidl CH uses HTML receipts (ticketType="HTML"); older/other countries may use JSON
    if raw.get("ticketType") == "HTML":
        raw_items = _parse_html_items(raw.get("htmlPrintedReceipt", ""))
    else:
        raw_lines = raw.get("itemsLines") or raw.get("lineItems") or raw.get("items") or []
        raw_items = [
            {
                "name": (l.get("description") or l.get("name") or l.get("title") or "Unknown"),
                "quantity": float(l.get("quantity") or 1),
                "price": float(l.get("currentUnitPrice") or l.get("unitPrice") or l.get("price") or 0),
                "art_id": str(l.get("codeInput") or l.get("articleId") or l.get("id") or ""),
            }
            for l in raw_lines if isinstance(l, dict)
        ]

    item_rows = [
        {
            "id": f"{receipt_id}_{i}",
            "receipt_id": receipt_id,
            "name": item["name"],
            "quantity": item["quantity"],
            "price": item["price"],
            "discount": item.get("discount", 0.0),
            "art_id": item.get("art_id", ""),
        }
        for i, item in enumerate(raw_items)
    ]

    return receipt_row, item_rows
