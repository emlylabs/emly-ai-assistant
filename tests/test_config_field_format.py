"""Field-format rules R16–R23 (``services/config_validation.py``).

These block every config save and can't be forced, so a false positive
locks an admin out of saving their bot. Each rule gets a good and a bad
value; colours get the broader set the widget actually renders.
"""
from __future__ import annotations

import pytest

from services.config_validation import (
    FIELD_FORMAT_RULE_PREFIXES,
    is_valid_color,
    validate_config,
)


def _format_keys(config: dict) -> set[str]:
    return {
        i.key for i in validate_config(config)
        if i.severity == "error" and i.key.startswith(FIELD_FORMAT_RULE_PREFIXES)
    }


@pytest.mark.parametrize("value", [
    "white", "WHITE", "rebeccapurple", "transparent", "currentColor",
    "#fff", "#ffff", "#1a73e8", "#1a73e8cc",
    "rgb(26, 115, 232)", "rgba(26,115,232,.5)", "rgb(0 0 0 / 50%)",
    "rgb(999,999,999)",  # valid CSS; the browser clamps
    "hsl(120deg 50% 50%)", "hsla(120, 50%, 50%, 0.3)",
    "hwb(194 0% 0%)", "lab(52% 40 59)", "lch(52% 72 56)",
    "oklab(59% 0.1 0.1)", "oklch(70% 0.1 200)", "oklch(70% 0.1 none)",
    "var(--brand)", "var(--brand, #fff)",
    "color-mix(in srgb, red 50%, blue)", "color(display-p3 1 0 0)",
    "  #fff  ",
])
def test_valid_colors_accepted(value):
    assert is_valid_color(value)


@pytest.mark.parametrize("value", [
    "notacolor", "#ggg", "#12", "rgb()", "rgb(,,,)", "rgb(1,2)",
    "rgb(1,2,3,4,5)", "rgb(a,b,c)",
    "red; background:url(x)", "var(--x);}", "var(brand)",
    "color-mix(in srgb, red; blue)", "rgb(1,2,3", "javascript:alert(1)",
])
def test_invalid_colors_rejected(value):
    assert not is_valid_color(value)


def test_named_theme_colour_saves():
    """Regression: "white" was stored by the widget editor before R18 and
    must not turn every later save of that bot into a 422."""
    config = {"widget": {"theme": {"header_background": "white", "header_foreground": "var(--fg)"}}}
    assert not any(k.startswith("R18_") for k in _format_keys(config))


def test_bad_theme_colour_flagged_per_field():
    config = {"widget": {"theme": {"header_background": "nope", "bot_message_foreground": "#fff"}}}
    keys = _format_keys(config)
    assert "R18_invalid_color_header_background" in keys
    assert "R18_invalid_color_bot_message_foreground" not in keys


@pytest.mark.parametrize("config,key,bad", [
    ({"support_email": "help@example.com"}, "R16_invalid_support_email", False),
    ({"support_email": "a@example.com, b@example.org"}, "R16_invalid_support_email", False),
    ({"support_email": "a@example.com, nope"}, "R16_invalid_support_email", True),
    ({"widget": {"logo": "https://example.com/logo.png"}}, "R17_invalid_logo_url", False),
    ({"widget": {"logo": "data:image/png;base64,AAAA"}}, "R17_invalid_logo_url", False),
    ({"widget": {"logo": "example.com/logo.png"}}, "R17_invalid_logo_url", True),
    ({"widget": {"logo": "/static/logo.png"}}, "R17_invalid_logo_url", False),
    ({"widget": {"logo": "./logo.png"}}, "R17_invalid_logo_url", False),
    ({"widget": {"logo": "../img/logo.png"}}, "R17_invalid_logo_url", False),
    ({"widget": {"logo": "//cdn.example.com/logo.png"}}, "R17_invalid_logo_url", False),
    ({"widget": {"logo": "/static/my logo.png"}}, "R17_invalid_logo_url", True),
    ({"widget": {"logo": "javascript:alert(1)"}}, "R17_invalid_logo_url", True),
    ({"llm": {"api_endpoint": "https://api.example.com/v1"}}, "R19_invalid_api_endpoint", False),
    ({"llm": {"api_endpoint": "api.example.com"}}, "R19_invalid_api_endpoint", True),
    ({"llm": {"temperature": 0}}, "R20_temperature_range", False),
    ({"llm": {"temperature": 2}}, "R20_temperature_range", False),
    ({"llm": {"temperature": 2.1}}, "R20_temperature_range", True),
    ({"llm": {"temperature": -0.1}}, "R20_temperature_range", True),
    ({"rag": {"top_k": 1}}, "R21_top_k_range", False),
    ({"rag": {"top_k": 50}}, "R21_top_k_range", False),
    ({"rag": {"top_k": 0}}, "R21_top_k_range", True),
    ({"rag": {"top_k": 51}}, "R21_top_k_range", True),
    ({"rag": {"embedding_threshold": 0.5}}, "R22_threshold_range", False),
    ({"rag": {"embedding_threshold": 1.5}}, "R22_threshold_range", True),
    ({"limits": {"max_file_size_mb": 10}}, "R23_max_file_size_range", False),
    ({"limits": {"max_file_size_mb": 0}}, "R23_max_file_size_range", True),
])
def test_field_format_rules(config, key, bad):
    assert (key in _format_keys(config)) is bad


def test_absent_fields_raise_nothing():
    assert _format_keys({}) == set()
