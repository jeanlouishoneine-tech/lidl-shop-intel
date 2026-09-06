# Lidl Spending Dashboard

A local web dashboard that connects to your **Lidl Plus** account to visualise your purchase history, track prices over time, surface personalised deals, and generate AI-powered spending insights — all running on your machine with no data sent to the cloud.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.12+** | Check with `python --version` |
| **[uv](https://docs.astral.sh/uv/)** | Fast Python package manager (`pip install uv`) |
| **[Ollama](https://ollama.com)** | Local AI runtime for the AI Insights tab |
| **Lidl Plus account** | The account you use in the Lidl Plus app |
| **Playwright browsers** | Installed automatically on first auth run |

Install the `llama3.2` model in Ollama before using the AI tab:

```bash
ollama pull llama3.2
```

---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd lidl-app

# 2. Install dependencies
uv sync

# 3. Copy the environment template and fill in your values
cp .env.example .env
```

Open `.env` and set your locale:

```env
LIDL_LANGUAGE=de
LIDL_COUNTRY=DE
LIDL_STORE_ID=        # see below
OLLAMA_MODEL=llama3.2
# LIDL_CURRENCY=EUR   # optional — derived automatically from LIDL_COUNTRY
```

> **Security note:** `.env` contains your Lidl refresh token and is git-ignored by default.
> Never commit it, share it, or store it in a public location.
> Anyone with this token can access your Lidl Plus account.

---

## Authentication

Run the one-time login script. A browser window will open for you to log in to Lidl Plus; it closes automatically when done and saves a refresh token to your `.env`.

```bash
uv run python src/auth.py
```

### Finding your store ID

```bash
uv run python -c "from src.api import fetch_stores; import json; print(json.dumps(fetch_stores()[:5], indent=2))"
```

Copy the `id` of your nearest store and set `LIDL_STORE_ID` in `.env`.

---

## Running the dashboard

```bash
uv run python src/app.py
```

Then open [http://localhost:8050](http://localhost:8050) in your browser.

Click **↻ Sync Data** in the top-right corner to pull your receipts, offers, and coupons from Lidl Plus.

---

## Tabs

### Overview

High-level KPIs for the selected time period: total spent, number of visits, average basket size, biggest single shop, and total discounts received. An interactive daily spend chart lets you click any point to drill into the full receipt for that day.

### Items

A deeper breakdown of your shopping habits:

- **Shopping patterns** — monthly spend bar chart and visits by day of the week.
- **Top items** — the 15 products you buy most often and the 15 you spend the most on.
- **Your staples** — items that appear in at least 20% of your trips, ranked by how often you buy them.
- **Price tracker** — pick any item to see how its unit price has changed across your purchases.
- **Price trends** — the items whose prices have risen or fallen the most since you first bought them.

### Offers for You

All current and upcoming Lidl Plus offers and coupons for your store, with three layers of information:

- **Your deals** — a prioritised table of active discounts on items you already buy regularly, sorted by how often you buy them.
- **Coupons** — activate or deactivate Lidl Plus coupons directly from the dashboard (no need to open the Lidl app).
- **Store offers** — the full catalogue of active and upcoming promotions for your store, with items you usually buy highlighted.

### AI Insights

Sends a structured summary of your spending data to a local [Ollama](https://ollama.com) model (default: `llama3.2`) and returns a plain-language analysis covering:

- Where your money goes and which habits drive costs.
- Deals and coupons worth acting on this week.
- Cheaper variant swaps for products you already buy.
- Pack-size value comparisons (price per kg / litre).
- Items whose prices have been creeping up.

Everything runs locally — your receipt data never leaves your machine.

---

## Project structure

```
src/
  app.py    # Dash app, layout, and callbacks
  api.py    # Lidl Plus API client (receipts, offers, coupons)
  auth.py   # One-time authentication script
  db.py     # SQLite persistence and queries
  ai.py     # Ollama integration
  assets/   # CSS design system (tokens, base, components) — auto-loaded by Dash
data/
  lidl.db   # Local SQLite database (git-ignored)
tests/
  test_db.py           # DB query tests
  test_api_parsing.py  # Receipt parsing tests
  test_api_errors.py   # API error handling tests
```

## Architecture

```
┌─────────────┐    HTTPS     ┌─────────────────────┐
│  Lidl Plus  │◄────────────►│  api.py              │
│  API        │              │  (receipts, offers,  │
└─────────────┘              │   coupons)           │
                             └──────────┬──────────┘
                                        │ parse_receipt()
                             ┌──────────▼──────────┐
                             │  db.py  (SQLite)     │
                             │  data/lidl.db        │
                             └──────────┬──────────┘
                                        │ query functions
                    ┌───────────────────┼──────────────────┐
                    │                   │                  │
           ┌────────▼──────┐   ┌────────▼───────┐  ┌──────▼──────┐
           │  app.py       │   │  ai.py          │  │  auth.py    │
           │  Dash UI      │   │  Ollama         │  │  One-time   │
           │  :8050        │   │  :11434         │  │  login      │
           └───────────────┘   └────────────────┘  └─────────────┘
```

## Docker

```bash
docker build -t lidl-app .
docker run -p 8050:8050 -v $(pwd)/data:/app/data --env-file .env lidl-app
```

The `data/` directory and `.env` are not baked into the image — mount them at runtime.

---

## Troubleshooting

### Ollama issues
- **Model not found:** run `ollama pull llama3.2`
- **Could not reach Ollama:** run `brew services start ollama` (macOS) or `ollama serve`
- **Slow responses:** llama3.2 needs ~4 GB RAM; close other applications

### Token and authentication
- **No LIDL\_REFRESH\_TOKEN found:** run `uv run python src/auth.py` again
- **Token expired** (after ~90 days): re-run the auth script — it overwrites the old token

### Common errors
- **No data in tabs:** click ↻ Sync Data first
- **No store ID configured** on Offers tab: set `LIDL_STORE_ID` in `.env` (see Installation)
- **Port 8050 in use:** another Dash app is running; kill it or change the port in `src/app.py`

---

## License

MIT
