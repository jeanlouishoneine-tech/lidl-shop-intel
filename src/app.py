"""
Lidl Spending Dashboard — entry point.
Run with: uv run python src/app.py
"""
import logging
import os
from pathlib import Path
from typing import Any

import dash
import plotly.graph_objects as go
import plotly.io as pio
from dash import ALL, Input, Output, State, callback, dcc, html
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

import api as lidl_api  # noqa: E402
import db  # noqa: E402
from ai import get_spending_insights  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")

_REQUIRED_ENV_VARS = ["LIDL_REFRESH_TOKEN", "LIDL_LANGUAGE", "LIDL_COUNTRY"]


def _validate_env() -> None:
    """Exit early with a helpful message if required env vars are missing."""
    missing = [k for k in _REQUIRED_ENV_VARS if not os.getenv(k)]
    if missing:
        raise SystemExit(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in the values."
        )


_validate_env()

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


# ── design tokens ────────────────────────────────────────────────────────────
# All DOM styling lives in src/assets/*.css as custom properties, switched by the
# #app-root[data-theme] attribute. Plotly renders to static SVG at call time and
# can't read CSS variables, so this is the one place colour values are duplicated
# — kept intentionally small, and it must be kept in sync with 01-tokens.css.
_PALETTE = {
    "light": {
        "text": "#16191F", "muted": "#6B7280", "border": "#E5E7EB", "surface": "#FFFFFF",
        "accent": "#0A4C93", "pos": "#15803D", "neg": "#DC2626",
    },
    "dark": {
        "text": "#E6E8EB", "muted": "#9AA1AC", "border": "#262B33", "surface": "#14171D",
        "accent": "#4D94E0", "pos": "#4ADE80", "neg": "#F87171",
    },
}


def _colors(dark: bool) -> dict[str, str]:
    """Palette for Plotly figures only. DOM elements are styled entirely via CSS
    custom properties, which repaint on theme change with no re-render needed —
    figures are static SVG, so they're the only thing that has to be redrawn."""
    return _PALETTE["dark" if dark else "light"]


def _register_plotly_templates() -> None:
    for theme, c in _PALETTE.items():
        tmpl = go.layout.Template()
        tmpl.layout = go.Layout(
            colorway=[c["accent"], c["pos"], c["neg"]],
            font=dict(family="Inter, system-ui, sans-serif", size=12, color=c["muted"]),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=8, r=16, t=8, b=8),
            hoverlabel=dict(
                bgcolor=c["surface"], bordercolor=c["border"],
                font=dict(family="IBM Plex Mono, monospace", size=12, color=c["text"]),
            ),
            xaxis=dict(
                gridcolor=c["border"], zerolinecolor=c["border"], automargin=True,
                tickfont=dict(family="IBM Plex Mono, monospace", size=11, color=c["muted"]),
                title=dict(font=dict(size=12, color=c["muted"])),
            ),
            yaxis=dict(
                gridcolor=c["border"], zerolinecolor=c["border"], automargin=True,
                tickfont=dict(family="IBM Plex Mono, monospace", size=11, color=c["muted"]),
                title=dict(font=dict(size=12, color=c["muted"])),
            ),
        )
        pio.templates[f"lidl_{theme}"] = tmpl


_register_plotly_templates()


# ── icons ─────────────────────────────────────────────────────────────────────
# Dash 4's html module has no SVG element components (no html.Svg/Path/…), so this
# is a small hand-rolled outline set: plain <span>s colored via `currentColor` and
# shaped by a CSS mask (see .icon--* in src/assets/03-components.css). No emoji,
# no icon-font dependency.

def _icon(name: str) -> html.Span:
    return html.Span(className=f"icon icon--{name}")


def _error_card(exc: Exception) -> html.Div:
    return html.Div(
        [
            html.Strong([_icon("alert-triangle"), " Something went wrong"], className="icon-text"),
            html.P(str(exc), className="error-card__message"),
        ],
        className="error-card",
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _kpi_card(label: str, value: str) -> html.Div:
    return html.Div(
        [
            html.P(label, className="kpi-card__label"),
            html.H3(value, className="kpi-card__value"),
        ],
        className="card kpi-card",
    )


def _chart_card(graph: dcc.Graph, title: str | None = None) -> html.Div:
    children: list[Any] = []
    if title:
        children.append(html.P(title, className="chart-card__title"))
    children.append(graph)
    return html.Div(children, className="card chart-card")


def _section(title: str) -> html.Div:
    return html.Div(title, className="section")


def _empty_state(message: str) -> html.P:
    return html.P(message, className="text-muted")


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
    id="app-root",
    **{"data-theme": "light"},  # type: ignore[arg-type]  # Dash's wildcard `data-*`/`aria-*` prop
    # mechanism needs the literal hyphenated key, which only dict-unpacking can express — the
    # generated stubs type each keyword-arg slot individually, so mypy checks the dict's `str`
    # value against every other typed prop's signature and flags the mismatches. Verified this
    # is the only way to set it: html.Div(data_theme=...) raises "unexpected keyword argument".
    children=[
        dcc.Store(id="db-version", data=0),
        dcc.Store(id="theme", data="light"),
        dcc.Download(id="download-csv"),

        # ── header ────────────────────────────────────────────────────────────
        html.Div(
            className="app-header",
            children=html.Div(
                className="container app-header__row",
                children=[
                    html.H1("Lidl Spending Dashboard", className="app-header__title"),
                    html.Div(className="app-header__spacer"),
                    dcc.Loading(
                        id="sync-loading",
                        type="circle",
                        children=html.Span(id="sync-status", className="app-header__status"),
                    ),
                    html.Div(
                        className="app-header__actions",
                        children=[
                            html.Button(
                                [_icon("refresh"), html.Span("Sync data", className="btn__label")],
                                id="sync-btn", n_clicks=0, title="Sync data",
                                className="btn btn--secondary",
                            ),
                            html.Button(
                                [_icon("download"), html.Span("Export CSV", className="btn__label")],
                                id="export-btn", n_clicks=0, title="Export CSV",
                                className="btn btn--secondary",
                            ),
                            html.Button(
                                [_icon("moon"), _icon("sun")], id="theme-toggle-btn", n_clicks=0,
                                title="Toggle dark mode",
                                className="btn btn--ghost btn--icon theme-toggle",
                            ),
                        ],
                    ),
                ],
            ),
        ),

        # ── time filter bar ───────────────────────────────────────────────────
        html.Div(
            id="filter-bar",
            className="filter-bar",
            children=html.Div(
                className="container filter-bar__row",
                children=[
                    html.Span("Time period:", className="filter-bar__label"),
                    dcc.Dropdown(
                        id="time-filter",
                        options=TIME_OPTIONS,
                        value="30",
                        clearable=False,
                        searchable=False,
                        className="filter-bar__select",
                    ),
                ],
            ),
        ),

        # coupon action feedback bar (hidden until a coupon is toggled)
        html.Div(id="coupon-feedback", className="container coupon-feedback"),

        # ── tabs ──────────────────────────────────────────────────────────────
        html.Div(
            id="tabs-container",
            className="tabs-container",
            children=html.Div(
                className="container",
                children=[
                    dcc.Tabs(
                        id="tabs", value="overview",
                        children=[
                            dcc.Tab(label="Overview",       value="overview"),
                            dcc.Tab(label="Items",          value="items"),
                            dcc.Tab(label="Offers for You", value="offers"),
                            dcc.Tab(label="AI Insights",    value="ai"),
                        ],
                    ),
                    html.Div(id="tab-content", className="tab-content"),
                ],
            ),
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
def on_sync(n_clicks: int, version: int) -> tuple[str, int]:
    return _sync_data(), (version or 0) + 1


@callback(
    Output("download-csv", "data"),
    Input("export-btn", "n_clicks"),
    State("time-filter", "value"),
    prevent_initial_call=True,
)
def export_csv(n_clicks: int, time_value: str | None) -> Any:
    import csv
    import io
    days = _parse_days(time_value)
    rows = db.export_items(days)
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return dcc.send_string(buf.getvalue(), filename="lidl_spending.csv")


@callback(
    Output("app-root", "data-theme"),
    Output("theme", "data"),
    Input("theme-toggle-btn", "n_clicks"),
    State("theme", "data"),
    prevent_initial_call=True,
)
def toggle_theme(n_clicks: int, current_theme: str) -> tuple[str, str]:
    """Flip the #app-root[data-theme] attribute. Every CSS-styled element repaints
    immediately via the custom-property cascade — no re-render needed. Only Plotly
    figures (static SVG) still need `render_tab` to re-run, which is why theme is
    wired there as an Input rather than a State."""
    new_theme = "dark" if current_theme == "light" else "light"
    return new_theme, new_theme


@callback(
    Output("tab-content", "children"),
    Input("tabs", "value"),
    Input("db-version", "data"),
    Input("time-filter", "value"),
    Input("theme", "data"),
)
def render_tab(tab: str, _version: int | None, time_value: str | None, theme: str | None) -> Any:
    try:
        days = _parse_days(time_value)
        dark = (theme == "dark")
        if tab == "overview":
            return _overview_tab(days, dark)
        if tab == "items":
            return _items_tab(days, dark)
        if tab == "offers":
            return _offers_tab(days, dark)
        if tab == "ai":
            return _ai_tab()
        return html.Div("Unknown tab")
    except Exception as exc:
        logger.exception("render_tab failed for tab=%s", tab)
        return _error_card(exc)


# ── tab renderers ─────────────────────────────────────────────────────────────

def _overview_tab(days: int | None, dark: bool = False) -> Any:
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
            line=dict(width=2),
            marker=dict(size=5),
        ))
        fig.update_layout(
            template=f"lidl_{'dark' if dark else 'light'}",
            xaxis_title="Date", yaxis_title=CURRENCY,
            hovermode="x unified",
        )
        chart = _chart_card(
            dcc.Graph(
                id="trend-chart", figure=fig,
                config={"displayModeBar": False},
                style={"cursor": "pointer"},
            ),
            title=f"Daily spend — {period}",
        )
    else:
        chart = html.Div(_empty_state("No data yet — click Sync data to fetch your receipts."), className="card")

    return html.Div([
        html.Div(
            className="kpi-grid",
            children=[
                _kpi_card("Total spent",     f"{currency} {spent:.2f}"),
                _kpi_card("Visits",          str(visits)),
                _kpi_card("Avg per visit",   f"{currency} {avg:.2f}"),
                _kpi_card("Biggest shop",    f"{currency} {biggest:.2f}"),
                _kpi_card("Total discounts", f"{currency} {discounts:.2f}"),
            ],
        ),
        chart,
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


def _parse_size(name: str) -> tuple[str, float] | None:
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
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = _SIZE_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    for w in s.split():
        if len(w) > 2 and w not in _STOPWORDS:
            return w
    return ""


def _build_value_analysis(days: int | None) -> str:
    """Structured facts on variant swaps, pack-size value, rising prices, cheapest-ever."""
    all_prices = db.item_avg_prices(days)

    families: dict[str, list[dict]] = {}
    for s in all_prices:
        stem = _family_stem(s["name"])
        if stem:
            families.setdefault(stem, []).append(s)

    swap_lines, pack_lines = [], []
    for stem, variants in families.items():
        common = sorted((v for v in variants if v["frequency"] >= 3),
                        key=lambda v: v["avg_price"])
        if len(common) >= 2 and common[-1]["avg_price"] - common[0]["avg_price"] >= 0.30:
            parts = [f'{v["name"]} (bought {v["frequency"]}x, avg {CURRENCY} {v["avg_price"]:.2f})'
                     for v in common]
            swap_lines.append((common[0]["frequency"] + common[-1]["frequency"],
                               f"  - {stem}: " + " vs ".join(parts)))

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

    swap_lines = [line for _, line in sorted(swap_lines, key=lambda x: x[0], reverse=True)]  # type: ignore[misc]

    rising = [t for t in db.price_trends(days, min_dates=3) if t["pct_change"] > 0][:6]
    rising_lines = [
        f'  - {t["name"]}: {CURRENCY} {t["first_price"]:.2f} → {t["last_price"]:.2f} (+{t["pct_change"]:.0f}%)'
        for t in rising
    ]

    ref_lines = [
        f'  - {s["name"]}: usually {CURRENCY} {s["avg_price"]:.2f}, lowest {CURRENCY} {s["min_price"]:.2f}, '
        f'last paid {CURRENCY} {s["last_price"]:.2f}'
        for s in db.item_price_stats(days, limit=8) if s.get("last_price") is not None
    ]

    out = ["CHEAPER VARIANT CANDIDATES (same first word — ignore if not truly the same product):"]
    out += swap_lines[:8] or ["  (none)"]  # type: ignore[arg-type, list-item]
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


def _deal_cards(deals: list[dict], matched_titles: set[str], kind: str,
                currency: str = CURRENCY) -> list[html.Div]:
    cards = []
    for d in deals:
        is_match = d["title"] in matched_titles
        badges = []
        disc_label = d.get("discount_msg", "") or d.get("discount_title", "")
        if disc_label:
            badges.append(html.Span(disc_label, className="badge badge--discount"))
        if is_match:
            badges.append(html.Span("Yours", className="badge badge--yours"))

        if kind == "offer":
            op = d.get("offer_price")
            orip = d.get("original_price")
            price_parts = []
            if op:
                price_parts.append(html.Span(f"{currency} {op:.2f}", className="deal-card__price-now"))
            if orip:
                price_parts.append(html.Span(f"{currency} {orip:.2f}", className="deal-card__price-was"))
            if op and orip and orip > op:
                price_parts.append(html.Span(f"save {currency} {orip - op:.2f}", className="deal-card__save"))
            price_block: Any = html.Div(price_parts, className="deal-card__price")
            desc = d.get("description", "")
            action = None

        else:  # coupon
            price_block = html.P(
                f"{d.get('discount_title','')}  {d.get('discount_desc','')}".strip(),
                className="deal-card__price",
                style={"fontWeight": 700},
            )
            desc = ""
            promo_id = d.get("promotion_id", "")
            if d.get("is_activated"):
                action = html.Button(
                    [_icon("check"), "Activated — deactivate"],
                    id={"type": "coupon-btn", "index": f"{promo_id}|deactivate"},
                    n_clicks=0,
                    className="btn btn--secondary btn--sm",
                )
            else:
                action = html.Button(
                    "Activate coupon",
                    id={"type": "coupon-btn", "index": f"{promo_id}|activate"},
                    n_clicks=0,
                    className="btn btn--primary btn--sm",
                )

        until = _format_date(d.get("valid_until", ""))

        cards.append(html.Div(
            className="deal-card" + (" deal-card--matched" if is_match else ""),
            children=[
                html.Div(badges, className="deal-card__badges"),
                html.Div(d["title"], className="deal-card__title"),
                html.Div(desc, className="deal-card__desc") if desc else "",
                price_block,
                html.Div(f"Until {until}" if until else "", className="deal-card__until"),
                action or "",
            ],
        ))
    return cards


def _offers_tab(days: int | None, dark: bool = False) -> Any:
    store_id = os.getenv("LIDL_STORE_ID", "")
    if not store_id:
        return html.Div([
            html.Strong([_icon("alert-triangle"), " No store ID configured"], className="icon-text"),
            html.P("Add LIDL_STORE_ID=CH0187 to your .env, then click Sync data.",
                   className="text-muted", style={"marginTop": 4}),
        ])

    cur_offers  = db.current_offers()
    upd_offers  = db.upcoming_offers()
    cur_coupons = db.current_coupons()
    upd_coupons = db.upcoming_coupons()

    if not cur_offers and not cur_coupons and not upd_offers and not upd_coupons:
        return _empty_state("No deals loaded yet — click Sync data.")

    currency  = CURRENCY
    purchased = db.purchased_article_ids(days)

    all_deals = (
        [{"kind": "offer",  **o} for o in cur_offers + upd_offers] +
        [{"kind": "coupon", **c_} for c_ in cur_coupons + upd_coupons]
    )
    matched = _match_deals(purchased, all_deals)
    matched_sorted = sorted(matched, key=lambda x: x[0]["frequency"], reverse=True)

    matched_offer_titles  = {d["title"] for _, d in matched if d.get("kind") == "offer"}
    matched_coupon_titles = {d["title"] for _, d in matched if d.get("kind") == "coupon"}

    def _save_str(deal):
        if deal.get("kind") == "offer" and deal.get("offer_price") and deal.get("original_price"):
            saving = deal["original_price"] - deal["offer_price"]
            return f"− {currency} {saving:.2f}" if saving > 0 else ""
        return ""

    def _action_cell(deal):
        if deal.get("kind") != "coupon":
            return html.Td("")
        promo_id = deal.get("promotion_id", "")
        if deal.get("is_activated"):
            btn = html.Button([_icon("check"), "On"],
                              id={"type": "coupon-btn", "index": f"{promo_id}|deactivate"},
                              n_clicks=0,
                              className="btn btn--positive btn--sm")
        else:
            btn = html.Button("Activate",
                              id={"type": "coupon-btn", "index": f"{promo_id}|activate"},
                              n_clicks=0,
                              className="btn btn--primary btn--sm")
        return html.Td(btn)

    deal_rows = [html.Tr([
        html.Th("Item"),
        html.Th("Bought",      className="num"),
        html.Th("Deal"),
        html.Th("Discount"),
        html.Th("You save",    className="num"),
        html.Th("Valid until"),
        html.Th(""),
    ])]
    for item, deal in matched_sorted:
        discount = deal.get("discount_msg") or deal.get("discount_title", "")
        deal_rows.append(html.Tr([
            html.Td(item["name"]),
            html.Td(f'{item["frequency"]}×',               className="num"),
            html.Td(deal["title"]),
            html.Td(discount, className="badge--discount-cell"),
            html.Td(_save_str(deal), className="num badge--save"),
            html.Td(_format_date(deal.get("valid_until", ""))),
            _action_cell(deal),
        ]))

    your_deals = html.Div(
        html.Table(deal_rows, className="data-table"),
        className="card table-card",
    ) if matched_sorted else _empty_state("No active deals match your purchase history.")

    def _cards_block(deals, kind):
        mt = matched_offer_titles if kind == "offer" else matched_coupon_titles
        cards = _deal_cards(deals, mt, kind, currency)
        return html.Div(cards, className="deal-grid")

    def _sub_section(label, deals, kind):
        if not deals:
            return html.Div()
        return html.Div([
            html.Div(label, className="label-eyebrow"),
            _cards_block(deals, kind),
        ], style={"marginBottom": 20})

    return html.Div([
        _section(f"Your deals ({len(matched)} matches)"),
        your_deals,

        _section(f"Coupons  ({len(cur_coupons)} active · {len(upd_coupons)} upcoming)"),
        _sub_section(f"Active ({len(cur_coupons)})",   cur_coupons, "coupon"),
        _sub_section(f"Coming soon ({len(upd_coupons)})", upd_coupons, "coupon"),

        _section(f"Store offers  ({len(cur_offers)} active · {len(upd_offers)} upcoming)"),
        _sub_section(f"Active ({len(cur_offers)})",    cur_offers,  "offer"),
        _sub_section(f"Coming soon ({len(upd_offers)})",  upd_offers,  "offer"),
    ])


def _price_trends_charts(days: int | None, dark: bool = False) -> Any:
    c = _colors(dark)
    trends = db.price_trends(days=days, min_dates=3)
    going_up   = [t for t in trends if t["pct_change"] >  1][:10]
    going_down = [t for t in trends if t["pct_change"] < -1][-10:][::-1]

    def _hover(t):
        return (f"{t['name']}<br>"
                f"{CURRENCY} {t['first_price']:.2f} → {CURRENCY} {t['last_price']:.2f}<br>"
                f"over {t['num_dates']} purchases<extra></extra>")

    if not going_up and not going_down:
        return _empty_state("Not enough price history in this period (need ≥3 purchases per item).")

    template = f"lidl_{'dark' if dark else 'light'}"
    charts = []

    if going_up:
        up_fig = go.Figure(go.Bar(
            x=[t["pct_change"] for t in going_up],
            y=[t["name"]       for t in going_up],
            orientation="h",
            marker_color=c["neg"],
            text=[f"+{t['pct_change']:.1f}%" for t in going_up],
            textposition="outside",
            textfont=dict(family="IBM Plex Mono, monospace", size=11, color=c["muted"]),
            customdata=[[t["first_price"], t["last_price"], t["num_dates"]] for t in going_up],
            hovertemplate=[_hover(t) for t in going_up],
        ))
        up_fig.update_layout(
            template=template,
            xaxis_title="Price change %",
            yaxis={"categoryorder": "total ascending"},
            height=max(260, len(going_up) * 38),
        )
        charts.append(_chart_card(dcc.Graph(figure=up_fig, config={"displayModeBar": False}), "↑ Trending up"))

    if going_down:
        dn_fig = go.Figure(go.Bar(
            x=[abs(t["pct_change"]) for t in going_down],
            y=[t["name"]            for t in going_down],
            orientation="h",
            marker_color=c["pos"],
            text=[f"{t['pct_change']:.1f}%" for t in going_down],
            textposition="outside",
            textfont=dict(family="IBM Plex Mono, monospace", size=11, color=c["muted"]),
            customdata=[[t["first_price"], t["last_price"], t["num_dates"]] for t in going_down],
            hovertemplate=[_hover(t) for t in going_down],
        ))
        dn_fig.update_layout(
            template=template,
            xaxis_title="Price change %",
            yaxis={"categoryorder": "total ascending"},
            height=max(260, len(going_down) * 38),
        )
        charts.append(_chart_card(dcc.Graph(figure=dn_fig, config={"displayModeBar": False}), "↓ Trending down"))

    return html.Div(charts, className="chart-row")


def _items_tab(days: int | None, dark: bool = False) -> Any:
    c = _colors(dark)
    template = f"lidl_{'dark' if dark else 'light'}"
    period    = PERIOD_LABEL.get(days, f"last {days} days")
    top_freq  = db.top_items_by_frequency(limit=15, days=days)
    top_spend = db.top_items_by_spend(limit=15, days=days)

    if not top_freq:
        return _empty_state("No item data for this period.")

    monthly = db.monthly_spend(days)
    monthly_fig = go.Figure(go.Bar(
        x=[r["month"]  for r in monthly],
        y=[r["total"]  for r in monthly],
        hovertemplate=f"%{{x}}: {CURRENCY} %{{y:.2f}}<extra></extra>",
    ))
    monthly_fig.update_layout(
        template=template,
        xaxis_title="Month", yaxis_title=CURRENCY,
        height=280,
    )

    weekday = db.visits_by_weekday(days)
    weekday_fig = go.Figure(go.Bar(
        x=[r["day"]    for r in weekday],
        y=[r["visits"] for r in weekday],
        hovertemplate="%{x}: %{y} visits<extra></extra>",
    ))
    weekday_fig.update_layout(
        template=template,
        yaxis_title="Visits",
        height=280,
    )

    freq_fig = go.Figure(go.Bar(
        x=[r["frequency"] for r in top_freq],
        y=[r["name"]      for r in top_freq],
        orientation="h",
        hovertemplate="%{y}: %{x}x<extra></extra>",
    ))
    freq_fig.update_layout(
        template=template,
        xaxis_title="Times bought",
        yaxis={"categoryorder": "total ascending"},
        height=460,
    )

    spend_fig = go.Figure(go.Bar(
        x=[r["total_spent"] for r in top_spend],
        y=[r["name"]        for r in top_spend],
        orientation="h",
        customdata=[r["frequency"] for r in top_spend],
        text=[f"×{r['frequency']}" for r in top_spend],
        textposition="outside",
        textfont=dict(family="IBM Plex Mono, monospace", size=11, color=c["muted"]),
        hovertemplate=f"%{{y}}<br>{CURRENCY} %{{x:.2f}} · bought %{{customdata}}×<extra></extra>",
    ))
    spend_fig.update_layout(
        template=template,
        xaxis_title=f"{CURRENCY} spent",
        yaxis={"categoryorder": "total ascending"},
        height=460,
    )

    staples = db.staple_items(days=days, min_pct=20)
    staples_table = html.Div(
        html.Table(
            [html.Tr([
                html.Th("Item"),
                html.Th("Visits",      className="num"),
                html.Th("% of trips",  className="num"),
                html.Th("Total spent", className="num"),
            ])] +
            [html.Tr([
                html.Td(r["name"]),
                html.Td(str(r["visits"]),                    className="num"),
                html.Td(f"{r['pct']}%",                      className="num"),
                html.Td(f"{CURRENCY} {r['total_spent']:.2f}", className="num"),
            ]) for r in staples],
            className="data-table",
        ),
        className="card table-card",
    ) if staples else _empty_state("No staples found for this period.")

    item_options = [
        {"label": r["name"], "value": r["item_key"]}
        for r in db.item_price_stats(limit=100, days=days)
    ]

    return html.Div([
        _section("Shopping patterns"),
        html.Div(
            className="chart-row",
            children=[
                _chart_card(dcc.Graph(figure=monthly_fig, config={"displayModeBar": False}), f"Monthly spend — {period}"),
                _chart_card(dcc.Graph(figure=weekday_fig, config={"displayModeBar": False}), f"Visits by day of week — {period}"),
            ],
        ),

        _section("Top items"),
        html.Div(
            className="chart-row",
            children=[
                _chart_card(dcc.Graph(figure=freq_fig,  config={"displayModeBar": False}), f"Most frequently bought — {period}"),
                _chart_card(dcc.Graph(figure=spend_fig, config={"displayModeBar": False}), f"Highest total spend — {period}"),
            ],
        ),

        _section("Your staples  (bought on ≥20% of trips)"),
        staples_table,

        _section("Price tracker"),
        html.P("Select an item to see how its unit price has changed over time.",
               className="text-muted", style={"marginBottom": 8}),
        dcc.Dropdown(
            id="price-item-selector",
            options=item_options,
            placeholder="Choose an item…",
            clearable=True,
            searchable=True,
            className="filter-bar__select",
            style={"maxWidth": 420, "width": "100%"},
        ),
        html.Div(id="price-tracker-chart", style={"marginTop": 12}),

        _section("Price trends"),
        html.P(
            "Items with the biggest price movement between their first and most recent purchase "
            "(minimum 3 distinct purchase dates).",
            className="text-muted", style={"marginBottom": 16},
        ),
        _price_trends_charts(days, dark),
    ])


def _ai_tab() -> Any:
    return html.Div([
        html.P(
            "Ask the local AI (llama3.2) to analyse your spending and flag the active & upcoming "
            "offers and coupons worth acting on for the selected time period.",
            className="text-muted",
        ),
        html.Button(
            "Analyse my spending", id="ai-btn", n_clicks=0,
            className="btn btn--primary", style={"marginTop": 12},
        ),
        dcc.Loading(
            children=html.Div(id="ai-output", style={"marginTop": 20}),
        ),
    ])


@callback(
    Output("ai-output", "children"),
    Input("ai-btn", "n_clicks"),
    State("time-filter", "value"),
    prevent_initial_call=True,
)
def run_ai(n_clicks: int, time_value: str | None) -> Any:
    try:
        days    = _parse_days(time_value)
        summary = db.spending_summary_text(days=days, currency=CURRENCY)
        value   = _build_value_analysis(days)
        deals   = _build_deals_summary(days)
        result  = get_spending_insights(summary, deals, value, currency=CURRENCY)
        return dcc.Markdown(result, className="card ai-card")
    except Exception as exc:
        logger.exception("run_ai failed")
        return _error_card(exc)


# ── price tracker ─────────────────────────────────────────────────────────────

@callback(
    Output("price-tracker-chart", "children"),
    Input("price-item-selector", "value"),
    State("time-filter", "value"),
    State("theme", "data"),
    prevent_initial_call=True,
)
def update_price_tracker(item_name: str | None, time_value: str | None, theme: str | None) -> Any:
    try:
        if not item_name:
            return None
        dark = (theme == "dark")
        c = _colors(dark)
        days    = _parse_days(time_value)
        history = db.price_history_for_item(item_name, days)
        if not history:
            return _empty_state("No price history for this item in the selected period.")

        dates  = [r["date"]  for r in history]
        prices = [r["price"] for r in history]
        min_p, max_p = min(prices), max(prices)

        fig = go.Figure(go.Scatter(
            x=dates, y=prices, mode="lines+markers",
            line=dict(width=2),
            marker=dict(size=7),
            hovertemplate=f"%{{x}}: {CURRENCY} %{{y:.2f}}<extra></extra>",
        ))
        if min_p != max_p:
            fig.add_hline(y=min_p, line_dash="dot", line_color=c["pos"],
                          annotation_text=f"Lowest {CURRENCY} {min_p:.2f}", annotation_position="bottom right")
            fig.add_hline(y=max_p, line_dash="dot", line_color=c["neg"],
                          annotation_text=f"Highest {CURRENCY} {max_p:.2f}", annotation_position="top right")
        fig.update_layout(
            template=f"lidl_{'dark' if dark else 'light'}",
            xaxis_title="Date", yaxis_title=CURRENCY,
            height=300,
        )
        return _chart_card(dcc.Graph(figure=fig, config={"displayModeBar": False}), f"Unit price history — {item_name}")
    except Exception as exc:
        logger.exception("update_price_tracker failed")
        return _error_card(exc)


# ── coupon activate / deactivate ──────────────────────────────────────────────

@callback(
    Output("coupon-feedback", "children"),
    Output("db-version", "data"),
    Input({"type": "coupon-btn", "index": ALL}, "n_clicks"),
    State("db-version", "data"),
    prevent_initial_call=True,
)
def toggle_coupon(n_clicks_list: list[int | None], version: int) -> tuple[Any, Any]:
    try:
        import dash
        ctx = dash.callback_context
        if not ctx.triggered_id or not isinstance(ctx.triggered_id, dict):
            return dash.no_update, dash.no_update
        # An ALL-pattern Input fires not just on a real click but whenever the set of
        # matched components changes shape — e.g. simply opening the Offers tab mounts
        # dozens of coupon buttons and Dash re-runs this callback with every n_clicks
        # still at 0. A genuine click is the only thing that pushes an n_clicks above 0,
        # so require that before touching the live activate/deactivate API. (We can't
        # trust ctx.triggered[0]["value"] here: the same coupon button is rendered in
        # both the deal cards and the matched-items table, and with duplicate
        # pattern-matching ids Dash reports the untouched copy's 0 for a real click.)
        if not any(n_clicks_list):
            return dash.no_update, dash.no_update

        index = ctx.triggered_id["index"]
        promotion_id, action = index.split("|", 1)
        # The activation endpoint keys on the coupon's own `id`, but the button
        # carries the `promotion_id` used elsewhere in the UI — resolve it here.
        coupon_id = db.coupon_id_for_promotion(promotion_id) or promotion_id

        if action == "activate":
            success = lidl_api.activate_coupon(coupon_id)
            icon, msg = ("check", "Coupon activated") if success else ("x", "Activation failed")
        else:
            success = lidl_api.deactivate_coupon(coupon_id)
            icon, msg = ("check", "Coupon deactivated") if success else ("x", "Deactivation failed")

        if success:
            db.set_coupon_activated(promotion_id, action == "activate")

        variant = "feedback--positive" if success else "feedback--negative"
        feedback = html.Span([_icon(icon), msg], className=f"feedback {variant}")
        return feedback, (version or 0) + 1
    except Exception as exc:
        logger.exception("toggle_coupon failed")
        return _error_card(exc), version


# ── receipt drill-down ────────────────────────────────────────────────────────

@callback(
    Output("day-detail", "children"),
    Input("trend-chart", "clickData"),
    prevent_initial_call=True,
)
def show_day_detail(click_data: dict[str, Any] | None) -> Any:
    try:
        if not click_data:
            return None

        date = click_data["points"][0]["x"]
        receipts = db.receipts_for_date(date)
        if not receipts:
            return _empty_state(f"No receipts found for {date}.")

        sections = []
        for receipt in receipts:
            items = db.items_for_receipt(receipt["id"])
            currency = receipt.get("currency", CURRENCY)

            rows = [
                html.Tr([
                    html.Th("Item"),
                    html.Th("Qty",        className="num"),
                    html.Th("Unit price", className="num"),
                    html.Th("Total",      className="num"),
                ])
            ]
            for item in items:
                gross    = item["quantity"] * item["price"]
                discount = item.get("discount", 0.0)
                net      = gross - discount
                qty_str  = f"×{item['quantity']:.0f}" if item["quantity"] == int(item["quantity"]) else f"×{item['quantity']:.3f}"
                rows.append(html.Tr([
                    html.Td(item["name"]),
                    html.Td(qty_str,                           className="num"),
                    html.Td(f"{currency} {item['price']:.2f}", className="num"),
                    html.Td(
                        html.Span([
                            f"{currency} {net:.2f}",
                            html.Span(f"−{currency} {discount:.2f}", className="receipt-discount")
                            if discount > 0 else "",
                        ]),
                        className="num",
                    ),
                ]))

            rows.append(html.Tr(
                [
                    html.Td(f"{len(items)} items", className="text-muted"),
                    html.Td("", className="num"),
                    html.Td("Total", className="num"),
                    html.Td(f"{currency} {receipt['total']:.2f}", className="num"),
                ],
                className="total-row",
            ))

            store = receipt.get("store_name") or receipt.get("store_id") or "Lidl"
            sections.append(html.Div([
                html.Div([
                    html.Span(store, className="receipt__store"),
                    html.Span(date, className="receipt__date"),
                ], className="receipt__header"),
                html.Table(rows, className="receipt-table"),
            ], className="receipt"))

        return html.Div(sections)
    except Exception as exc:
        logger.exception("show_day_detail failed")
        return _error_card(exc)


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, port=8050)
