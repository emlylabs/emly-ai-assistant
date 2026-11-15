"""G7a: model prices for the analytics spend figures.

Spend used to be computed in the browser from a price list hard-coded
for a handful of OpenAI model names (at prices that have since
changed), so Azure deployments, OpenRouter models and self-hosted models
showed no cost at all.

Prices now live in the workspace settings under ``model_prices``,
editable by superadmins on the Workspace page::

    {"gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.6},
     "my-llama":    {"input_per_1m": 0,    "output_per_1m": 0}}

USD per million tokens. A model name matches exactly (ignoring case), or
by its last segment, so ``gpt-4o-mini`` prices ``openai/gpt-4o-mini`` and
the other way round. A model with no price has no cost (shown as "—"),
never $0: set 0 explicitly for a free self-hosted model.

Costs count the tokens recorded on answers only; routing, detail
extraction, summaries and enrichment calls don't record tokens.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

SETTINGS_KEY = "model_prices"
MAX_MODELS = 200
MAX_PRICE_PER_1M = 10_000.0


class InvalidModelPrices(ValueError):
    pass


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0 <= value <= MAX_PRICE_PER_1M else None


def validate(value: Any) -> None:
    """Raise InvalidModelPrices unless ``value`` is a well-formed table."""
    if not isinstance(value, dict):
        raise InvalidModelPrices("Must be an object mapping model names to prices.")
    if len(value) > MAX_MODELS:
        raise InvalidModelPrices(f"At most {MAX_MODELS} models.")
    for model, price in value.items():
        if not isinstance(model, str) or not model.strip() or len(model) > 128:
            raise InvalidModelPrices("Model names must be 1 to 128 characters.")
        if not isinstance(price, dict) or set(price) - {"input_per_1m", "output_per_1m"}:
            raise InvalidModelPrices(f'"{model}": give input_per_1m and output_per_1m only.')
        for key in ("input_per_1m", "output_per_1m"):
            if _number(price.get(key)) is None:
                raise InvalidModelPrices(
                    f'"{model}": {key} must be a number from 0 to {MAX_PRICE_PER_1M:g} (USD per 1M tokens).')


def _table() -> Dict[str, Tuple[float, float]]:
    from models.workspace_settings import WorkspaceSettingsStore

    raw = (WorkspaceSettingsStore.get().settings or {}).get(SETTINGS_KEY)
    out: Dict[str, Tuple[float, float]] = {}
    if not isinstance(raw, dict):
        return out
    for model, price in raw.items():
        if not isinstance(model, str) or not isinstance(price, dict):
            continue
        i, o = _number(price.get("input_per_1m")), _number(price.get("output_per_1m"))
        if i is not None and o is not None:
            out[model.strip().lower()] = (i, o)
    return out


def _last_segment(name: str) -> str:
    return name.rsplit("/", 1)[-1]


class PriceTable:
    def __init__(self, table: Optional[Dict[str, Tuple[float, float]]] = None):
        self._table = _table() if table is None else table
        self._by_tail: Dict[str, Tuple[float, float]] = {}
        for name, price in self._table.items():
            self._by_tail.setdefault(_last_segment(name), price)

    def price(self, model: Optional[str]) -> Optional[Tuple[float, float]]:
        if not model:
            return None
        name = model.strip().lower()
        return self._table.get(name) or self._by_tail.get(_last_segment(name))

    def cost_usd(self, model: Optional[str], prompt_tokens: int, completion_tokens: int) -> Optional[float]:
        price = self.price(model)
        if price is None:
            return None
        return round((prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000, 6)
