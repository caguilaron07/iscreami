"""AI-powered ingredient enrichment using the Anthropic API."""
from __future__ import annotations

from typing import Any, cast

import anthropic
from anthropic.types import ToolParam

from api.models import Ingredient
from api.settings import settings

_ESTIMABLE_FIELDS = [
    "water_pct",
    "total_fat_pct",
    "total_sugar_pct",
    "protein_pct",
    "carbohydrate_pct",
    "sodium_mg",
    "lactose_pct",
    "sucrose_pct",
    "glucose_pct",
    "fructose_pct",
    "milk_fat_pct",
    "msnf_pct",
]

_ENRICHMENT_TOOL: ToolParam = {  # type: ignore[assignment]
    "name": "estimate_ingredient_composition",
    "description": (
        "Estimate missing nutritional composition values for an ice cream ingredient. "
        "Use null for fields you cannot estimate confidently."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "water_pct": {"type": ["number", "null"], "description": "Water, grams per 100g"},
            "total_fat_pct": {"type": ["number", "null"], "description": "Total fat, grams per 100g"},
            "total_sugar_pct": {"type": ["number", "null"], "description": "Total sugars, grams per 100g"},
            "protein_pct": {"type": ["number", "null"], "description": "Protein, grams per 100g"},
            "carbohydrate_pct": {"type": ["number", "null"], "description": "Carbohydrates (inc. fiber), grams per 100g"},
            "sodium_mg": {
                "type": ["number", "null"],
                "description": "Sodium - MILLIGRAMS per 100g (NOT grams, NOT percent). Typical range 1-2000 mg/100g.",
            },
            "lactose_pct": {"type": ["number", "null"], "description": "Lactose, grams per 100g"},
            "sucrose_pct": {"type": ["number", "null"], "description": "Sucrose, grams per 100g"},
            "glucose_pct": {"type": ["number", "null"], "description": "Glucose (dextrose), grams per 100g"},
            "fructose_pct": {"type": ["number", "null"], "description": "Fructose, grams per 100g"},
            "milk_fat_pct": {"type": ["number", "null"], "description": "Milk fat (butterfat), grams per 100g — dairy only"},
            "msnf_pct": {"type": ["number", "null"], "description": "Milk solids non-fat (MSNF), grams per 100g — dairy only"},
            "pac_override": {
                "type": ["number", "null"],
                "description": (
                    "PAC override — ONLY for non-standard solutes (polyols, sugar alcohols, "
                    "salt, alcohol) where composition-based calculation is insufficient. "
                    "PAC factors: sucrose=100, fructose/glucose/allulose=190, erythritol=280, "
                    "glycerin=372, NaCl=585, ethanol=743. Leave null for standard ingredients."
                ),
            },
            "pod_override": {
                "type": ["number", "null"],
                "description": (
                    "POD (sweetness) override — ONLY if sweetness significantly differs from "
                    "what the sugar breakdown would calculate. Leave null for standard ingredients."
                ),
            },
        },
    },
}


def enrich_ingredient(ingredient: Ingredient) -> dict[str, Any]:
    """Estimate None-valued composition fields via Anthropic API.

    Only fills fields that are currently None — never touches fields set to 0 or
    any other non-None value.

    Returns a dict of {field_name: estimated_value} to apply to the ingredient.

    Raises:
        ValueError: ANTHROPIC_API_KEY not configured.
        anthropic.APIError: API call failed (covers RateLimitError, APITimeoutError, etc.).
    """
    all_estimable = [*_ESTIMABLE_FIELDS, "pac_override", "pod_override"]
    missing = [f for f in all_estimable if getattr(ingredient, f, None) is None]
    # Only proceed if there are missing composition fields (not just override fields)
    missing_composition = [f for f in _ESTIMABLE_FIELDS if getattr(ingredient, f, None) is None]
    if not missing_composition:
        return {}

    if not settings.anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not configured. "
            "Add it to .env to use ingredient enrichment."
        )

    known = {
        f: getattr(ingredient, f)
        for f in all_estimable
        if getattr(ingredient, f, None) is not None
    }
    known_str = ", ".join(f"{k}={v}" for k, v in known.items()) if known else "none known"

    system = (
        "You are a food scientist specialising in ice cream ingredients. "
        "Estimate missing nutritional values using USDA FoodData Central data where available. "
        "Unit rules: all _pct fields are grams per 100g of ingredient. "
        "sodium_mg is MILLIGRAMS per 100g — do NOT return it as a percentage or decimal grams. "
        "PAC factors: sucrose=100, fructose/glucose/allulose=190, erythritol=280, "
        "glycerin=372, NaCl=585, ethanol=743. "
        "Only set pac_override/pod_override for non-standard solutes where the standard "
        "calculation is clearly insufficient."
    )
    user_msg = (
        f"Ingredient: {ingredient.name}\n"
        f"Already known: {known_str}\n"
        f"Fields to estimate: {', '.join(missing)}\n"
        "Call estimate_ingredient_composition with your best estimates."
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        tools=[_ENRICHMENT_TOOL],
        tool_choice=cast(Any, {"type": "any"}),
    )

    tool_block = next(
        (b for b in response.content if b.type == "tool_use"),
        None,
    )
    if tool_block is None:
        return {}

    return {
        field: value
        for field, value in tool_block.input.items()
        if value is not None and getattr(ingredient, field, "SENTINEL") is None
    }
