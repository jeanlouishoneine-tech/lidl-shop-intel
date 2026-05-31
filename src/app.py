"""
Lidl Spending Dashboard — entry point.
Run with: uv run python src/app.py
"""
import os
from pathlib import Path

import dash
from dash import ALL, Input, Output, State, callback, dcc, html
import plotly.graph_objects as go
from dotenv import load_dotenv

import db
import api as lidl_api
from ai import get_spending_insights

load_dotenv(Path(__file__).parent.parent / ".env")

_COUNTRY_CURRENCY = {
    "DE": "EUR", "AT": "EUR", "FR": "EUR", "ES": "EUR", "IT": "EUR",
    "NL": "EUR", "BE": "EUR", "PT": "EUR", "IE": "EUR", "FI": "EUR",
    "LU": "EUR", "SK": "EUR", "SI": "EUR", "HR": "EUR", "LT": "EUR",
    "LV": "EUR", "EE": "EUR", "GR": "EUR", "CY": "EUR",
    "CH": "CHF", "GB": "GBP", "PL": "PLN", "CZ": "CZK",
    "HU": "HUF", "RO": "RON", "BG": "BGN",
}
CURRENCY = (
    os.getenv("LIDL_CURRENCY")
    or _COUNTRY_CURRENCY.get(os.getenv("LIDL_COUNTRY", "DE"), "EUR")
)

db.init_db()

# ── constants ─────────────────────────────────────────────────────────────────
LIDL_BLUE   = "#0050AA"
LIDL_YELLOW = "#FFD700"
BG          = "#F8F9FA"
CARD_BG     = "#FFFFFF"

TIME_OPTIONS = [
    {"label": "Last week",     "value": "7"},
    {"label": "Last month",    "value": "30"},
    {"label": "Last 2 months", "value": "60"},
    {"label": "Last 3 months", "value": "90"},
    {"label": "Last 6 months", "value": "180"},
    {"label": "Last year",     "value": "365"},
    {"label": "All time",      "value": "0"},
]

PERIOD_LABEL = {
    7: "last 7 days", 30: "last 30 days", 60: "last 60 days",
    90: "last 90 days", 180: "last 6 months", 365: "last year",
    None: "all time",
}


def _parse_days(value: str | None) -> int | None:
    n = int(value or "30")
    return None if n == 0 else n


# ── helpers ───────────────────────────────────────────────────────────────────

def _kpi_card(label: str, value: str) -> html.Div:
    return html.Div(
        [
            html.P(label, style={"margin": 0, "fontSize": 13, "color": "#666"}),
            html.H3(value, style={"margin": "4px 0 0", "color": LIDL_BLUE}),
        ],
        style={
            "background": CARD_BG,
            "borderRadius": 8,
            "padding": "16px 20px",
            "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
            "flex": 1,
            "minWidth": 140,
        },
    )


def _chart_card(graph: dcc.Graph) -> html.Div:
    return html.Div(
        graph,
        style={
            "background": CARD_BG,
            "borderRadius": 8,
            "padding": 16,
            "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
            "flex": 1,
            "minWidth": 320,
        },
    )


def _sync_data() -> str:
    """Fetch new receipts + offers from Lidl and persist to SQLite."""
    try:
        headers = lidl_api.fetch_receipt_headers()
        known = db.receipt_ids()
        new_ids = [h["id"] for h in headers if h.get("id") and h["id"] not in known]

        for rid in new_ids:
            detail = lidl_api.fetch_receipt_detail(rid)
            receipt_row, item_rows = lidl_api.parse_receipt(detail)
            db.upsert_receipt(receipt_row)
            db.upsert_items(item_rows)

        # Back-fill items for receipts stored before the HTML parser existed.
        with_items = db.receipt_ids_with_items()
        needs_items = list(known - with_items)[:50]
        backfilled = 0
        for rid in needs_items:
            try:
                detail = lidl_api.fetch_receipt_detail(rid)
                _, item_rows = lidl_api.parse_receipt(detail)
                db.upsert_items(item_rows)
                backfilled += 1
            except Exception:
                pass

        offers = lidl_api.fetch_offers()
        db.upsert_offers(offers)

        coupons = lidl_api.fetch_coupons()
        db.upsert_coupons(coupons)

        parts = [f"{len(new_ids)} new receipts"]
        if backfilled:
            remaining = len(known - with_items) - backfilled
            parts.append(f"{backfilled} receipts back-filled")
            if remaining > 0:
                parts.append(f"{remaining} remaining — click Sync again")
        if offers:
            parts.append(f"{len(offers)} offers")
        if coupons:
            parts.append(f"{len(coupons)} coupons")
        return "Synced: " + ", ".join(parts) + "."
    except SystemExit as exc:
        return f"Auth required: {exc}"
    except Exception as exc:
        return f"Sync error: {exc}"


# ── layout ────────────────────────────────────────────────────────────────────

app = dash.Dash(__name__, title="Lidl Dashboard", suppress_callback_exceptions=True)

app.layout = html.Div(
    style={"fontFamily": "system-ui, sans-serif", "background": BG, "minHeight": "100vh"},
    children=[
        dcc.Store(id="db-version", data=0),

        # ── header ────────────────────────────────────────────────────────────
        html.Div(
            style={
                "background": LIDL_BLUE, "padding": "16px 32px",
                "display": "flex", "alignItems": "center", "gap": 16,
            },
            children=[
                html.Span("🛒", style={"fontSize": 28}),
                html.H1("Lidl Spending Dashboard",
                        style={"margin": 0, "color": "white", "fontSize": 22}),
                html.Div(style={"flex": 1}),
                html.Button(
                    "↻ Sync Data", id="sync-btn", n_clicks=0,
                    style={
                        "background": LIDL_YELLOW, "border": "none", "borderRadius": 6,
                        "padding": "8px 18px", "fontWeight": 600, "cursor": "pointer", "fontSize": 14,
                    },
                ),
                html.Span(id="sync-status",
                          style={"color": "#cce", "fontSize": 13, "marginLeft": 12}),
            ],
        ),

        # ── time filter bar ───────────────────────────────────────────────────
        html.Div(
            style={
                "background": CARD_BG, "borderBottom": "1px solid #dee2e6",
                "padding": "10px 32px", "display": "flex", "alignItems": "center", "gap": 12,
            },
            children=[
                html.Span("Time period:",
                          style={"fontSize": 14, "color": "#555", "fontWeight": 500, "whiteSpace": "nowrap"}),
                dcc.Dropdown(
                    id="time-filter",
                    options=TIME_OPTIONS,
                    value="30",
                    clearable=False,
                    searchable=False,
                    style={"width": 180, "fontSize": 14},
                ),
            ],
        ),

        # coupon action feedback bar (hidden until a coupon is toggled)
        html.Div(id="coupon-feedback", style={"padding": "6px 32px", "minHeight": 0}),

        # ── tabs ──────────────────────────────────────────────────────────────
        html.Div(
            style={"padding": "24px 32px"},
            children=[
                dcc.Tabs(
                    id="tabs", value="overview",
                    colors={"primary": LIDL_BLUE, "background": BG, "border": "#dee2e6"},
                    children=[
                        dcc.Tab(label="Overview",       value="overview"),
                        dcc.Tab(label="Items",          value="items"),
                        dcc.Tab(label="Offers for You", value="offers"),
                        dcc.Tab(label="AI Insights",    value="ai"),
                    ],
                ),
                html.Div(id="tab-content", style={"marginTop": 24}),
            ],
        ),
    ],
)


# ── callbacks ─────────────────────────────────────────────────────────────────

@callback(
    Output("sync-status", "children"),
    Output("db-version", "data"),
    Input("sync-btn", "n_clicks"),
    State("db-version", "data"),
    prevent_initial_call=True,
)
def on_sync(n_clicks, version):
    return _sync_data(), (version or 0) + 1


@callback(
    Output("tab-content", "children"),
    Input("tabs", "value"),
    Input("db-version", "data"),
    Input("time-filter", "value"),
)
def render_tab(tab, _version, time_value):
    days = _parse_days(time_value)
    if tab == "overview":
        return _overview_tab(days)
    if tab == "items":
        return _items_tab(days)
    if tab == "offers":
        return _offers_tab(days)
    if tab == "ai":
        return _ai_tab()
    return html.Div("Unknown tab")


# ── tab renderers ─────────────────────────────────────────────────────────────

def _overview_tab(days: int | None) -> html.Div:
    currency = CURRENCY
    period   = PERIOD_LABEL.get(days, f"last {days} days")

    spent     = db.total_spent(days)
    visits    = db.receipt_count(days)
    avg       = db.avg_per_visit(days)
    biggest   = db.biggest_purchase(days)
    discounts = db.total_discounts(days)

    by_day = db.spending_by_day(days)
    if by_day:
        dates  = [r["date"]  for r in by_day]
        totals = [r["total"] for r in by_day]
        fig = go.Figure(go.Scatter(
            x=dates, y=totals, mode="lines+markers",
            line=dict(color=LIDL_BLUE, width=2),
            marker=dict(size=5, color=LIDL_BLUE),
        ))
        fig.update_layout(
            title=f"Daily spend — {period}",
            xaxis_title="Date", yaxis_title=CURRENCY,
            plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
            margin=dict(l=0, r=0, t=40, b=0),
            hovermode="x unified",
        )
        chart = dcc.Graph(
            id="trend-chart", figure=fig,
            config={"displayModeBar": False},
            style={"cursor": "pointer"},
        )
    else:
        chart = html.P("No data yet — click Sync Data to fetch your receipts.",
                       style={"color": "#888"})

    return html.Div([
        html.Div(
            style={"display": "flex", "gap": 16, "flexWrap": "wrap", "marginBottom": 24},
            children=[
                _kpi_card("Total spent",      f"{currency} {spent:.2f}"),
                _kpi_card("Visits",           str(visits)),
                _kpi_card("Avg per visit",    f"{currency} {avg:.2f}"),
                _kpi_card("Biggest shop",     f"{currency} {biggest:.2f}"),
                _kpi_card("Total discounts",  f"{currency} {discounts:.2f}"),
            ],
        ),
        html.Div(chart, style={
            "background": CARD_BG, "borderRadius": 8,
            "padding": 16, "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
        }),
        html.Div(id="day-detail", style={"marginTop": 20}),
    ])


def _match_deals(purchased: dict[str, dict], deals: list[dict]) -> list[tuple[dict, dict]]:
    """
    Return (item, deal) pairs where a purchased article ID is on the deal — a true
    same-product match with no name guessing. `purchased` maps art_id → {name, frequency}.
    Offers carry comma-separated `product_ids`; coupons carry `article_ids`.
    """
    pairs = []
    seen = set()
    for deal in deals:
        id_field = deal.get("product_ids") if deal.get("kind") == "offer" else deal.get("article_ids")
        deal_ids = {x for x in (id_field or "").split(",") if x}
        for aid in deal_ids:
            entry = purchased.get(aid)
            if not entry:
                continue
            key = (aid, deal["id"])
            if key in seen:
                continue
            seen.add(key)
            pairs.append(({"name": entry["name"], "frequency": entry["frequency"], "art_id": aid}, deal))
    return pairs


def _format_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        from datetime import date
        return date.fromisoformat(iso[:10]).strftime("%-d %b")
    except Exception:
        return iso[:10]


def _discount_pct(deal: dict) -> int | None:
    """Parse a leading -NN% from an offer's discount_msg or a coupon's discount_title."""
    import re
    label = deal.get("discount_msg") or deal.get("discount_title") or ""
    m = re.search(r"-\s*(\d+)\s*%", label)
    return int(m.group(1)) if m else None


_SIZE_RE = __import__("re").compile(
    r"(\d+[.,]?\d*)\s*(kg|g|gr|cl|ml|l)\b", __import__("re").I
)
_STOPWORDS = {"de", "des", "du", "le", "la", "les", "a", "au", "aux", "en", "et",
              "d", "l", "bio", "the", "of", "premium", "terra", "natura"}


def _parse_size(name: str):
    """Return (base_unit_label, base_qty) for a weight/volume token, else None.
    Weight → ('kg', kilograms); volume → ('l', litres)."""
    m = _SIZE_RE.search(name)
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit in ("kg",):
        return ("kg", val)
    if unit in ("g", "gr"):
        return ("kg", val / 1000)
    if unit == "l":
        return ("l", val)
    if unit == "cl":
        return ("l", val / 100)
    if unit == "ml":
        return ("l", val / 1000)
    return None


def _family_stem(name: str) -> str:
    """First significant word of a normalized name — a coarse product-family key."""
    import re, unicodedata
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = _SIZE_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    for w in s.split():
        if len(w) > 2 and w not in _STOPWORDS:
            return w
    return ""


def _build_value_analysis(days: int | None) -> str:
    """Structured facts on variant swaps, pack-size value, rising prices, cheapest-ever."""
    all_prices = db.item_avg_prices(days)

    # Group into candidate families by first significant word.
    families: dict[str, list[dict]] = {}
    for s in all_prices:
        stem = _family_stem(s["name"])
        if stem:
            families.setdefault(stem, []).append(s)

    # 1 + 2. Variant swaps and pack-size value (families with >= 2 variants).
    swap_lines, pack_lines = [], []
    for stem, variants in families.items():
        # Variant swaps: only consider variants bought a meaningful number of times.
        common = sorted((v for v in variants if v["frequency"] >= 3),
                        key=lambda v: v["avg_price"])
        if len(common) >= 2 and common[-1]["avg_price"] - common[0]["avg_price"] >= 0.30:
            parts = [f'{v["name"]} (bought {v["frequency"]}x, avg {CURRENCY} {v["avg_price"]:.2f})'
                     for v in common]
            swap_lines.append((common[0]["frequency"] + common[-1]["frequency"],
                               f"  - {stem}: " + " vs ".join(parts)))

        # Pack-size value: any sized variants (these tend to be lower-frequency).
        per_unit = []
        for v in variants:
            if v["frequency"] < 2:
                continue
            sz = _parse_size(v["name"])
            if sz and sz[1] > 0:
                per_unit.append((v["name"], v["avg_price"] / sz[1], sz[0]))
        if len(per_unit) >= 2:
            per_unit.sort(key=lambda x: x[1])
            parts = [f"{n} = {CURRENCY} {p:.2f}/{u}" for n, p, u in per_unit]
            pack_lines.append(f"  - {stem}: " + " vs ".join(parts) +
                              f"  (best value: {per_unit[0][0]})")

    # Most-bought families first for the swap list.
    swap_lines = [line for _, line in sorted(swap_lines, key=lambda x: x[0], reverse=True)]

    # 3. Rising prices.
    rising = [t for t in db.price_trends(days, min_dates=3) if t["pct_change"] > 0][:6]
    rising_lines = [
        f'  - {t["name"]}: {CURRENCY} {t["first_price"]:.2f} → {t["last_price"]:.2f} (+{t["pct_change"]:.0f}%)'
        for t in rising
    ]

    # 4. Cheapest-ever reference for top staples.
    ref_lines = [
        f'  - {s["name"]}: usually {CURRENCY} {s["avg_price"]:.2f}, lowest {CURRENCY} {s["min_price"]:.2f}, '
        f'last paid {CURRENCY} {s["last_price"]:.2f}'
        for s in db.item_price_stats(days, limit=8) if s.get("last_price") is not None
    ]

    out = ["CHEAPER VARIANT CANDIDATES (same first word — ignore if not truly the same product):"]
    out += swap_lines[:8] or ["  (none)"]
    out.append("")
    out.append("PACK-SIZE VALUE (price per base unit):")
    out += pack_lines[:8] or ["  (none)"]
    out.append("")
    out.append("RISING PRICES (first → most recent):")
    out += rising_lines or ["  (none)"]
    out.append("")
    out.append("CHEAPEST-EVER REFERENCE (your most-bought staples):")
    out += ref_lines or ["  (none)"]
    return "\n".join(out)


def _build_deals_summary(days: int | None) -> str:
    """Plain-text block of habit-matched + standout deals for the AI prompt."""
    purchased = db.purchased_article_ids(days)

    all_deals = (
        [{"kind": "offer",  "timing": "active",   **o} for o in db.current_offers()] +
        [{"kind": "offer",  "timing": "upcoming", **o} for o in db.upcoming_offers()] +
        [{"kind": "coupon", "timing": "active",   **c} for c in db.current_coupons()] +
        [{"kind": "coupon", "timing": "upcoming", **c} for c in db.upcoming_coupons()]
    )
    if not all_deals:
        return "DEALS: none loaded (the shopper hasn't synced offers/coupons yet)."

    # One deal can match several similar items; keep only the most-bought match per deal.
    best_per_deal: dict[str, tuple[dict, dict]] = {}
    for item, deal in _match_deals(purchased, all_deals):
        existing = best_per_deal.get(deal["id"])
        if existing is None or item["frequency"] > existing[0]["frequency"]:
            best_per_deal[deal["id"]] = (item, deal)
    matched = list(best_per_deal.values())

    matched_ids = set(best_per_deal)
    standout = [d for d in all_deals
                if (_discount_pct(d) or 0) >= 40 and d["id"] not in matched_ids]

    def _line(item, deal):
        icon = "🏷" if deal["kind"] == "offer" else "🎟"
        tag  = "[ACTIVE]" if deal["timing"] == "active" else "[UPCOMING]"
        disc = deal.get("discount_msg") or deal.get("discount_title") or ""
        parts = [tag, icon, deal["title"], f"— {disc}" if disc else ""]
        if deal.get("offer_price"):
            op, orip = deal["offer_price"], deal.get("original_price")
            if orip and orip > op:
                parts.append(f"— {CURRENCY} {op:.2f} (was {orip:.2f}, save {orip - op:.2f})")
            else:
                parts.append(f"— {CURRENCY} {op:.2f}")
        when = _format_date(deal.get("valid_until", "")) if deal["timing"] == "active" \
            else _format_date(deal.get("valid_from", ""))
        if when:
            parts.append(f"— {'until' if deal['timing']=='active' else 'starts'} {when}")
        if item is not None:
            parts.append(f"— you buy {item['name']} {item['frequency']}x")
        if deal["kind"] == "coupon":
            parts.append("— COUPON: activate first")
        return " ".join(p for p in parts if p)

    lines = ["DEALS ON ITEMS YOU BUY REGULARLY:"]
    if matched:
        lines += [_line(item, deal) for item, deal in matched]
    else:
        lines.append("  (none match the shopper's regular purchases)")

    if standout:
        lines.append("")
        lines.append("STANDOUT DISCOUNTS (deep cuts, not their usual items):")
        lines += [_line(None, d) for d in standout]

    return "\n".join(lines)


def _deal_cards(deals: list[dict], matched_titles: set[str],
                kind: str, currency: str = CURRENCY) -> list[html.Div]:
    cards = []
    for d in deals:
        is_match = d["title"] in matched_titles
        border   = f"2px solid {LIDL_YELLOW}" if is_match else "1px solid #e0e0e0"
        yours_badge = html.Span("✓ Yours", style={
            "background": LIDL_YELLOW, "color": LIDL_BLUE,
            "fontSize": 10, "fontWeight": 700, "padding": "1px 6px",
            "borderRadius": 10, "marginLeft": 6,
        }) if is_match else ""

        disc_label = d.get("discount_msg","") or d.get("discount_title","")
        disc_badge = html.Span(disc_label, style={
            "background": "#e63946", "color": "white", "fontSize": 12,
            "fontWeight": 700, "padding": "2px 8px", "borderRadius": 10,
        }) if disc_label else ""

        if kind == "offer":
            op = d.get("offer_price")
            orip = d.get("original_price")
            price_parts = []
            if op:
                price_parts.append(html.Span(f"{currency} {op:.2f} ", style={"fontWeight":700,"color":LIDL_BLUE,"fontSize":15}))
            if orip:
                price_parts.append(html.Span(f"{currency} {orip:.2f}", style={"textDecoration":"line-through","color":"#aaa","fontSize":12}))
            if op and orip and orip > op:
                price_parts.append(html.Span(f" save {currency} {orip - op:.2f}", style={"color":"#2a9d2a","fontSize":11,"marginLeft":4}))
            price_block = html.Div(price_parts, style={"margin":"4px 0"})
            desc = d.get("description","")
            action = None

        else:  # coupon
            price_block = html.P(
                f"{d.get('discount_title','')}  {d.get('discount_desc','')}".strip(),
                style={"margin":"4px 0","fontWeight":700,"color":LIDL_BLUE,"fontSize":13},
            )
            desc = ""
            promo_id = d.get("promotion_id","")
            if d.get("is_activated"):
                action = html.Button(
                    "✓ Activated — Deactivate",
                    id={"type": "coupon-btn", "index": f"{promo_id}|deactivate"},
                    style={"fontSize":11,"padding":"4px 10px","cursor":"pointer","marginTop":6,
                           "background":"#f0f0f0","border":"1px solid #ccc","borderRadius":4,"color":"#555"},
                )
            else:
                action = html.Button(
                    "Activate coupon",
                    id={"type": "coupon-btn", "index": f"{promo_id}|activate"},
                    style={"fontSize":11,"padding":"4px 10px","cursor":"pointer","marginTop":6,
                           "background":LIDL_BLUE,"color":"white","border":"none","borderRadius":4},
                )

        until = _format_date(d.get("valid_until",""))

        cards.append(html.Div(
            style={"background":CARD_BG,"border":border,"borderRadius":8,"padding":"12px 14px",
                   "minWidth":190,"flex":"1 1 190px","maxWidth":260,
                   "boxShadow":"0 1px 3px rgba(0,0,0,.06)"},
            children=[
                html.Div([disc_badge, yours_badge], style={"marginBottom":6,"display":"flex","gap":4,"flexWrap":"wrap"}),
                html.Div(d["title"], style={"fontWeight":600,"fontSize":13,"marginBottom":2}),
                html.Div(desc, style={"fontSize":11,"color":"#666","marginBottom":2}) if desc else "",
                price_block,
                html.Div(f"Until {until}" if until else "", style={"fontSize":11,"color":"#999","marginTop":4}),
                action or "",
            ],
        ))
    return cards


def _offers_tab(days: int | None) -> html.Div:
    store_id = os.getenv("LIDL_STORE_ID", "")
    if not store_id:
        return html.Div([
            html.P("⚠️ No store ID configured.", style={"fontWeight": 600}),
            html.P("Add LIDL_STORE_ID=CH0187 to your .env then click ↻ Sync Data.", style={"color": "#555"}),
        ])

    cur_offers  = db.current_offers()
    upd_offers  = db.upcoming_offers()
    cur_coupons = db.current_coupons()
    upd_coupons = db.upcoming_coupons()

    if not cur_offers and not cur_coupons and not upd_offers and not upd_coupons:
        return html.P("No deals loaded yet — click ↻ Sync Data.", style={"color": "#888"})

    currency  = CURRENCY
    purchased = db.purchased_article_ids(days)

    all_deals = (
        [{"kind": "offer",  **o} for o in cur_offers + upd_offers] +
        [{"kind": "coupon", **c} for c in cur_coupons + upd_coupons]
    )
    matched = _match_deals(purchased, all_deals)
    matched_sorted = sorted(matched, key=lambda x: x[0]["frequency"], reverse=True)

    matched_offer_titles  = {d["title"] for _, d in matched if d.get("kind") == "offer"}
    matched_coupon_titles = {d["title"] for _, d in matched if d.get("kind") == "coupon"}

    # ── Section 1: Your deals ─────────────────────────────────────────────────
    th = {"padding": "8px 12px", "background": LIDL_BLUE, "color": "white",
          "fontSize": 13, "textAlign": "left"}
    td = {"padding": "7px 12px", "fontSize": 13, "borderBottom": "1px solid #f0f0f0"}
    tdr = {**td, "textAlign": "right"}

    def _save_str(deal):
        if deal.get("kind") == "offer" and deal.get("offer_price") and deal.get("original_price"):
            saving = deal["original_price"] - deal["offer_price"]
            return f"− {currency} {saving:.2f}" if saving > 0 else ""
        return ""

    def _action_cell(deal):
        if deal.get("kind") != "coupon":
            return html.Td("", style=td)
        promo_id = deal.get("promotion_id", "")
        if deal.get("is_activated"):
            btn = html.Button("✓ On", id={"type": "coupon-btn", "index": f"{promo_id}|deactivate"},
                              style={"fontSize": 11, "padding": "2px 8px", "cursor": "pointer",
                                     "background": "#e8f5e9", "border": "1px solid #2a9d2a",
                                     "borderRadius": 4, "color": "#2a9d2a"})
        else:
            btn = html.Button("Activate", id={"type": "coupon-btn", "index": f"{promo_id}|activate"},
                              style={"fontSize": 11, "padding": "2px 8px", "cursor": "pointer",
                                     "background": LIDL_BLUE, "border": "none",
                                     "borderRadius": 4, "color": "white"})
        return html.Td(btn, style=td)

    deal_rows = [html.Tr([
        html.Th("Item",        style=th),
        html.Th("Bought",      style={**th, "textAlign": "right"}),
        html.Th("Deal",        style=th),
        html.Th("Discount",    style=th),
        html.Th("You save",    style={**th, "textAlign": "right"}),
        html.Th("Valid until", style=th),
        html.Th("",            style=th),
    ])]
    for item, deal in matched_sorted:
        kind_icon = "🏷" if deal.get("kind") == "offer" else "🎟"
        discount  = deal.get("discount_msg") or deal.get("discount_title", "")
        deal_rows.append(html.Tr([
            html.Td(item["name"],                          style=td),
            html.Td(f'{item["frequency"]}×',               style=tdr),
            html.Td(f'{kind_icon} {deal["title"]}',        style=td),
            html.Td(discount, style={**td, "color": "#e63946", "fontWeight": 600}),
            html.Td(_save_str(deal), style={**tdr, "color": "#2a9d2a", "fontWeight": 600}),
            html.Td(_format_date(deal.get("valid_until", "")), style=td),
            _action_cell(deal),
        ]))

    your_deals = html.Div(
        html.Table(deal_rows, style={"width": "100%", "borderCollapse": "collapse"}),
        style={"overflowX": "auto"},
    ) if matched_sorted else html.P("No active deals match your purchase history.", style={"color": "#888"})

    # ── Section helpers ───────────────────────────────────────────────────────
    def _cards_block(deals, kind):
        matched_titles = matched_offer_titles if kind == "offer" else matched_coupon_titles
        cards = _deal_cards(deals, matched_titles, kind, currency)
        return html.Div(cards, style={"display": "flex", "gap": 10, "flexWrap": "wrap"})

    def _sub_section(label, deals, kind):
        if not deals:
            return html.Div()
        return html.Div([
            html.Div(label, style={"fontWeight": 600, "fontSize": 13, "color": "#555",
                                   "marginBottom": 10}),
            _cards_block(deals, kind),
        ], style={"marginBottom": 20})

    return html.Div([
        _section(f"🎯 Your deals ({len(matched)} matches)"),
        html.Div(your_deals, style={"background": CARD_BG, "borderRadius": 8, "padding": 16,
                                     "boxShadow": "0 1px 4px rgba(0,0,0,.08)", "marginBottom": 8}),

        _section(f"🎟 Coupons  ({len(cur_coupons)} active · {len(upd_coupons)} upcoming)"),
        _sub_section(f"Active ({len(cur_coupons)})",   cur_coupons, "coupon"),
        _sub_section(f"Coming soon ({len(upd_coupons)})", upd_coupons, "coupon"),

        _section(f"🏷 Store offers  ({len(cur_offers)} active · {len(upd_offers)} upcoming)"),
        _sub_section(f"Active ({len(cur_offers)})",    cur_offers,  "offer"),
        _sub_section(f"Coming soon ({len(upd_offers)})",  upd_offers,  "offer"),
    ])


def _price_trends_charts(days: int | None) -> html.Div:
    trends = db.price_trends(days=days, min_dates=3)
    going_up   = [t for t in trends if t["pct_change"] >  1][:10]
    going_down = [t for t in trends if t["pct_change"] < -1][-10:][::-1]  # most negative first

    def _hover(t):
        return (f"{t['name']}<br>"
                f"{CURRENCY} {t['first_price']:.2f} → {CURRENCY} {t['last_price']:.2f}<br>"
                f"over {t['num_dates']} purchases<extra></extra>")

    if not going_up and not going_down:
        return html.P(
            "Not enough price history in this period (need ≥3 purchases per item).",
            style={"color": "#888"},
        )

    charts = []

    if going_up:
        up_fig = go.Figure(go.Bar(
            x=[t["pct_change"] for t in going_up],
            y=[t["name"]       for t in going_up],
            orientation="h",
            marker_color="#e63946",
            text=[f"+{t['pct_change']:.1f}%" for t in going_up],
            textposition="auto",
            textfont=dict(color="white", size=11),
            customdata=[[t["first_price"], t["last_price"], t["num_dates"]] for t in going_up],
            hovertemplate=[_hover(t) for t in going_up],
        ))
        up_fig.update_layout(
            title="↑ Trending up",
            xaxis_title="Price change %",
            yaxis={"categoryorder": "total ascending"},
            plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
            margin=dict(l=0, r=0, t=40, b=0), height=max(260, len(going_up) * 38),
        )
        charts.append(_chart_card(dcc.Graph(figure=up_fig, config={"displayModeBar": False})))

    if going_down:
        dn_fig = go.Figure(go.Bar(
            x=[abs(t["pct_change"]) for t in going_down],
            y=[t["name"]            for t in going_down],
            orientation="h",
            marker_color="#2a9d2a",
            text=[f"{t['pct_change']:.1f}%" for t in going_down],
            textposition="auto",
            textfont=dict(color="white", size=11),
            customdata=[[t["first_price"], t["last_price"], t["num_dates"]] for t in going_down],
            hovertemplate=[_hover(t) for t in going_down],
        ))
        dn_fig.update_layout(
            title="↓ Trending down",
            xaxis_title="Price change %",
            yaxis={"categoryorder": "total ascending"},
            plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
            margin=dict(l=0, r=0, t=40, b=0), height=max(260, len(going_down) * 38),
        )
        charts.append(_chart_card(dcc.Graph(figure=dn_fig, config={"displayModeBar": False})))

    return html.Div(charts, style={"display": "flex", "gap": 16, "flexWrap": "wrap"})


def _section(title: str) -> html.Div:
    return html.Div(title, style={
        "fontWeight": 600, "fontSize": 15, "color": LIDL_BLUE,
        "borderBottom": f"2px solid {LIDL_YELLOW}",
        "paddingBottom": 6, "marginTop": 28, "marginBottom": 16,
    })


def _items_tab(days: int | None) -> html.Div:
    period    = PERIOD_LABEL.get(days, f"last {days} days")
    top_freq  = db.top_items_by_frequency(limit=15, days=days)
    top_spend = db.top_items_by_spend(limit=15, days=days)

    if not top_freq:
        return html.P("No item data for this period.", style={"color": "#888"})

    # ── shopping patterns ─────────────────────────────────────────────────────
    monthly = db.monthly_spend(days)
    monthly_fig = go.Figure(go.Bar(
        x=[r["month"]  for r in monthly],
        y=[r["total"]  for r in monthly],
        marker_color=LIDL_BLUE,
        hovertemplate=f"%{{x}}: {CURRENCY} %{{y:.2f}}<extra></extra>",
    ))
    monthly_fig.update_layout(
        title=f"Monthly spend — {period}",
        xaxis_title="Month", yaxis_title=CURRENCY,
        plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
        margin=dict(l=0, r=0, t=40, b=0), height=280,
    )

    weekday = db.visits_by_weekday(days)
    weekday_fig = go.Figure(go.Bar(
        x=[r["day"]    for r in weekday],
        y=[r["visits"] for r in weekday],
        marker_color=LIDL_YELLOW,
        marker_line_color=LIDL_BLUE, marker_line_width=1,
        hovertemplate="%{x}: %{y} visits<extra></extra>",
    ))
    weekday_fig.update_layout(
        title=f"Visits by day of week — {period}",
        yaxis_title="Visits",
        plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
        margin=dict(l=0, r=0, t=40, b=0), height=280,
    )

    # ── top items ─────────────────────────────────────────────────────────────
    freq_fig = go.Figure(go.Bar(
        x=[r["frequency"] for r in top_freq],
        y=[r["name"]      for r in top_freq],
        orientation="h", marker_color=LIDL_BLUE,
        hovertemplate="%{y}: %{x}x<extra></extra>",
    ))
    freq_fig.update_layout(
        title=f"Most frequently bought — {period}",
        xaxis_title="Times bought",
        yaxis={"categoryorder": "total ascending"},
        plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
        margin=dict(l=0, r=0, t=40, b=0), height=460,
    )

    spend_fig = go.Figure(go.Bar(
        x=[r["total_spent"] for r in top_spend],
        y=[r["name"]        for r in top_spend],
        orientation="h",
        marker_color=LIDL_YELLOW,
        marker_line_color=LIDL_BLUE, marker_line_width=1,
        customdata=[r["frequency"] for r in top_spend],
        text=[f"×{r['frequency']}" for r in top_spend],
        textposition="auto",
        textfont=dict(color=LIDL_BLUE, size=11),
        hovertemplate=f"%{{y}}<br>{CURRENCY} %{{x:.2f}} · bought %{{customdata}}×<extra></extra>",
    ))
    spend_fig.update_layout(
        title=f"Highest total spend — {period}",
        xaxis_title=f"{CURRENCY} spent",
        yaxis={"categoryorder": "total ascending"},
        plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
        margin=dict(l=0, r=0, t=40, b=0), height=460,
    )

    # ── staples ───────────────────────────────────────────────────────────────
    staples = db.staple_items(days=days, min_pct=20)
    th_s = {"padding": "8px 12px", "background": LIDL_BLUE, "color": "white",
            "fontSize": 13, "textAlign": "left"}
    td_s = {"padding": "6px 12px", "fontSize": 13, "borderBottom": "1px solid #f0f0f0"}
    td_r = {**td_s, "textAlign": "right"}
    staples_table = html.Div(
        html.Table(
            [html.Tr([
                html.Th("Item",          style=th_s),
                html.Th("Visits",        style={**th_s, "textAlign": "right"}),
                html.Th("% of trips",    style={**th_s, "textAlign": "right"}),
                html.Th("Total spent",   style={**th_s, "textAlign": "right"}),
            ])] +
            [html.Tr([
                html.Td(r["name"],                           style=td_s),
                html.Td(str(r["visits"]),                    style=td_r),
                html.Td(f"{r['pct']}%",                      style=td_r),
                html.Td(f"{CURRENCY} {r['total_spent']:.2f}",       style=td_r),
            ]) for r in staples],
            style={"width": "100%", "borderCollapse": "collapse"},
        ),
        style={"background": CARD_BG, "borderRadius": 8, "padding": 16,
               "boxShadow": "0 1px 4px rgba(0,0,0,.08)", "overflowX": "auto"},
    ) if staples else html.P("No staples found for this period.", style={"color": "#888"})

    # ── price tracker ─────────────────────────────────────────────────────────
    item_options = [{"label": r["name"], "value": r["name"]}
                    for r in db.top_items_by_frequency(limit=50, days=days)]

    return html.Div([
        _section("Shopping patterns"),
        html.Div(
            style={"display": "flex", "gap": 16, "flexWrap": "wrap"},
            children=[
                _chart_card(dcc.Graph(figure=monthly_fig, config={"displayModeBar": False})),
                _chart_card(dcc.Graph(figure=weekday_fig, config={"displayModeBar": False})),
            ],
        ),

        _section("Top items"),
        html.Div(
            style={"display": "flex", "gap": 16, "flexWrap": "wrap"},
            children=[
                _chart_card(dcc.Graph(figure=freq_fig,  config={"displayModeBar": False})),
                _chart_card(dcc.Graph(figure=spend_fig, config={"displayModeBar": False})),
            ],
        ),

        _section("Your staples  (bought on ≥20% of trips)"),
        staples_table,

        _section("Price tracker"),
        html.P("Select an item to see how its unit price has changed over time.",
               style={"color": "#555", "marginBottom": 8}),
        dcc.Dropdown(
            id="price-item-selector",
            options=item_options,
            placeholder="Choose an item…",
            clearable=True,
            searchable=True,
            style={"maxWidth": 420, "fontSize": 14},
        ),
        html.Div(id="price-tracker-chart", style={"marginTop": 12}),

        _section("Price trends"),
        html.P(
            "Items with the biggest price movement between their first and most recent purchase "
            "(minimum 3 distinct purchase dates).",
            style={"color": "#555", "marginBottom": 16},
        ),
        _price_trends_charts(days),
    ])




def _ai_tab() -> html.Div:
    return html.Div([
        html.P(
            "Ask the local AI (llama3.2) to analyse your spending and flag the active & upcoming "
            "offers and coupons worth acting on for the selected time period.",
            style={"color": "#555"},
        ),
        html.Button(
            "Analyse my spending", id="ai-btn", n_clicks=0,
            style={
                "background": LIDL_BLUE, "color": "white", "border": "none",
                "borderRadius": 6, "padding": "10px 20px",
                "fontWeight": 600, "cursor": "pointer", "fontSize": 14,
            },
        ),
        dcc.Loading(
            type="circle", color=LIDL_BLUE,
            children=html.Div(id="ai-output", style={"marginTop": 20}),
        ),
    ])


@callback(
    Output("ai-output", "children"),
    Input("ai-btn", "n_clicks"),
    State("time-filter", "value"),
    prevent_initial_call=True,
)
def run_ai(n_clicks, time_value):
    days    = _parse_days(time_value)
    summary = db.spending_summary_text(days=days, currency=CURRENCY)
    value   = _build_value_analysis(days)
    deals   = _build_deals_summary(days)
    result  = get_spending_insights(summary, deals, value, currency=CURRENCY)
    return dcc.Markdown(
        result,
        style={
            "background": CARD_BG, "borderRadius": 8, "padding": "4px 20px",
            "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
            "lineHeight": 1.6, "fontSize": 14,
        },
    )


# ── price tracker ─────────────────────────────────────────────────────────────

@callback(
    Output("price-tracker-chart", "children"),
    Input("price-item-selector", "value"),
    State("time-filter", "value"),
    prevent_initial_call=True,
)
def update_price_tracker(item_name, time_value):
    if not item_name:
        return None
    days    = _parse_days(time_value)
    history = db.price_history_for_item(item_name, days)
    if not history:
        return html.P("No price history for this item in the selected period.",
                      style={"color": "#888"})

    dates  = [r["date"]  for r in history]
    prices = [r["price"] for r in history]
    min_p, max_p = min(prices), max(prices)

    fig = go.Figure(go.Scatter(
        x=dates, y=prices, mode="lines+markers",
        line=dict(color=LIDL_BLUE, width=2),
        marker=dict(size=7, color=LIDL_BLUE),
        hovertemplate=f"%{{x}}: {CURRENCY} %{{y:.2f}}<extra></extra>",
    ))
    if min_p != max_p:
        fig.add_hline(y=min_p, line_dash="dot", line_color="#2a9d2a",
                      annotation_text=f"Lowest {CURRENCY} {min_p:.2f}", annotation_position="bottom right")
        fig.add_hline(y=max_p, line_dash="dot", line_color="#e63946",
                      annotation_text=f"Highest {CURRENCY} {max_p:.2f}", annotation_position="top right")
    fig.update_layout(
        title=f"Unit price history — {item_name}",
        xaxis_title="Date", yaxis_title=CURRENCY,
        plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG,
        margin=dict(l=0, r=0, t=40, b=0), height=300,
    )
    return _chart_card(dcc.Graph(figure=fig, config={"displayModeBar": False}))


# ── coupon activate / deactivate ──────────────────────────────────────────────

@callback(
    Output("coupon-feedback", "children"),
    Output("db-version", "data"),
    Input({"type": "coupon-btn", "index": ALL}, "n_clicks"),
    State("db-version", "data"),
    prevent_initial_call=True,
)
def toggle_coupon(n_clicks_list, version):
    import dash
    ctx = dash.callback_context
    if not ctx.triggered_id or not isinstance(ctx.triggered_id, dict):
        return dash.no_update, dash.no_update

    index = ctx.triggered_id["index"]
    promotion_id, action = index.split("|", 1)

    if action == "activate":
        success = lidl_api.activate_coupon(promotion_id)
        msg     = "✓ Coupon activated" if success else "✗ Activation failed"
    else:
        success = lidl_api.deactivate_coupon(promotion_id)
        msg     = "✓ Coupon deactivated" if success else "✗ Deactivation failed"

    if success:
        db.set_coupon_activated(promotion_id, action == "activate")

    color = "#2a9d2a" if success else "#e63946"
    feedback = html.Span(msg, style={"color": color, "fontWeight": 600, "fontSize": 14})
    return feedback, (version or 0) + 1


# ── receipt drill-down ────────────────────────────────────────────────────────

@callback(
    Output("day-detail", "children"),
    Input("trend-chart", "clickData"),
    prevent_initial_call=True,
)
def show_day_detail(click_data):
    if not click_data:
        return None

    date = click_data["points"][0]["x"]
    receipts = db.receipts_for_date(date)
    if not receipts:
        return html.P(f"No receipts found for {date}.", style={"color": "#888"})

    sections = []
    for receipt in receipts:
        items = db.items_for_receipt(receipt["id"])
        currency = receipt.get("currency", CURRENCY)

        th_style = {
            "padding": "8px 12px", "textAlign": "left",
            "background": LIDL_BLUE, "color": "white", "fontSize": 13,
        }
        td_style = {"padding": "6px 12px", "fontSize": 13, "borderBottom": "1px solid #f0f0f0"}
        td_num   = {**td_style, "textAlign": "right"}

        rows = [
            html.Tr([
                html.Th("Item",       style=th_style),
                html.Th("Qty",        style={**th_style, "textAlign": "right"}),
                html.Th("Unit price", style={**th_style, "textAlign": "right"}),
                html.Th("Total",      style={**th_style, "textAlign": "right"}),
            ])
        ]
        for item in items:
            gross    = item["quantity"] * item["price"]
            discount = item.get("discount", 0.0)
            net      = gross - discount
            qty_str  = f"×{item['quantity']:.0f}" if item["quantity"] == int(item["quantity"]) else f"×{item['quantity']:.3f}"
            rows.append(html.Tr([
                html.Td(item["name"],                      style=td_style),
                html.Td(qty_str,                           style=td_num),
                html.Td(f"{currency} {item['price']:.2f}", style=td_num),
                html.Td(
                    html.Span([
                        f"{currency} {net:.2f}",
                        html.Span(
                            f" (−{currency} {discount:.2f})",
                            style={"color": "#2a9d2a", "fontSize": 11, "marginLeft": 4},
                        ) if discount > 0 else "",
                    ]),
                    style=td_num,
                ),
            ]))

        # totals footer
        rows.append(html.Tr([
            html.Td(f"{len(items)} items", style={**td_style, "fontWeight": 600, "color": "#666"}),
            html.Td("", style=td_num),
            html.Td("Total", style={**td_num, "fontWeight": 600}),
            html.Td(f"{currency} {receipt['total']:.2f}",
                    style={**td_num, "fontWeight": 700, "color": LIDL_BLUE}),
        ]))

        store = receipt.get("store_name") or receipt.get("store_id") or "Lidl"
        sections.append(html.Div([
            html.H4(f"{store} — {date}",
                    style={"margin": "0 0 12px", "color": LIDL_BLUE, "fontSize": 15}),
            html.Table(rows, style={"width": "100%", "borderCollapse": "collapse"}),
        ]))

    return html.Div(
        sections,
        style={
            "background": CARD_BG, "borderRadius": 8, "padding": 20,
            "boxShadow": "0 1px 4px rgba(0,0,0,.08)",
        },
    )


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, port=8050)
