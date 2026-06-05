"""
Local AI spending insights powered by Ollama (free, runs on your Mac).
Requires Ollama to be running: brew services start ollama
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from ollama import Client, ResponseError

load_dotenv(Path(__file__).parent.parent / ".env")

_OLLAMA_HOST = "http://localhost:11434"
_DEFAULT_MODEL = "llama3.2"

def _system_prompt(currency: str) -> str:
    """
    Build the system prompt for the Ollama model.

    Instructs the model to produce exactly five markdown sections:
      1. Spending Snapshot   — total, visits, basket composition
      2. Smarter Buying      — variant swaps, pack-size value, cheapest-ever
      3. Grab This Trip      — active deals on regular purchases
      4. Plan Ahead          — upcoming deals and rising-price staples
      5. Standout Deals      — deep discounts (≥40%) outside usual items

    The user message sent alongside this prompt is assembled in app.py from:
      - db.spending_summary_text()   → purchase history
      - _build_value_analysis()      → variant/pack/price trend facts
      - _build_deals_summary()       → matched + standout deals text

    CRITICAL: the model must only reference prices and names that appear
    verbatim in the data — it must never invent figures.
    """
    return f"""You are a personal finance assistant helping a Lidl shopper spend more wisely.
You receive their recent purchase summary, a value-analysis block (variant swaps, pack-size value,
rising prices, cheapest-ever reference), and active/upcoming offers & coupons (deals on items they
buy + standout deep discounts).

Reply in EXACTLY these five markdown sections, in this order, each with its `##` heading. Under
each, use short bullet points. Bold the key item names and {currency} figures. Be specific — use
the real names and numbers from the data, never generic advice. Use {currency} for every amount.

## 📊 Spending Snapshot
- Total spent, number of visits, and what dominates their basket.

## 🥚 Smarter Buying
- Cheaper-variant swaps: only when two entries are genuinely the SAME kind of product (e.g.
  **Bananas** vs **Organic Bananas** — but NOT two unrelated products). Quantify the
  per-item difference in {currency}.
- Better-value pack sizes from PACK-SIZE VALUE (e.g. buy the size with the lower {currency}/kg).
- A cheapest-ever reference where useful (e.g. "you've paid as low as **{currency} X** for **item**").

## 🛒 Grab This Trip
- Active deals on items they actually buy. Quantify {currency} saved. If it's a COUPON, remind
  them to activate it first.

## 📅 Plan Ahead
- Upcoming deals worth waiting for (mention the start date) and rising-price staples worth buying
  before they climb further.

## 💡 Standout Deals
- Genuinely deep discounts (≈40%+), even outside their usual items.

If a section has no relevant data, keep its heading and write one short line saying so. Keep the
whole reply tight — a few bullets per section, no padding.

CRITICAL: Use ONLY item names, prices, sizes, and percentages that appear verbatim in the data.
Never invent a pack size, price, or figure that is not present. Copy numbers exactly."""


def get_spending_insights(spending_summary: str, deals_summary: str,
                          value_analysis: str = "", currency: str = "EUR") -> str:
    """
    Send spending data + value analysis + deals to the local Ollama model and return
    markdown insights. Returns an error string (not an exception) if Ollama is unavailable.
    """
    model = os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)

    user_message = f"{spending_summary}\n\n{value_analysis}\n\n{deals_summary}"

    try:
        client = Client(host=_OLLAMA_HOST)
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _system_prompt(currency)},
                {"role": "user", "content": user_message},
            ],
        )
        return response.message.content or ""
    except ResponseError as exc:
        if "model" in str(exc).lower():
            return (
                f"**Model `{model}` not found.**\n\n"
                f"Run: `ollama pull {model}`"
            )
        return f"**Ollama error:** {exc}"
    except Exception as exc:
        return (
            "**Could not reach Ollama.**\n\n"
            "Make sure it is running:\n```\nbrew services start ollama\n```\n\n"
            f"Details: {exc}"
        )
