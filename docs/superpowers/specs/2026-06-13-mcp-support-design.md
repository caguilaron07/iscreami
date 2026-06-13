# MCP Support Design

**Date:** 2026-06-13
**Status:** Approved — updated after design review

## Overview

Add MCP (Model Context Protocol) support to iscreami in two directions:

1. **iscreami as an MCP server** — expose full CRUD for ingredients, recipes, and profiles, plus recipe calculation, as MCP tools that any MCP-compatible AI client (Claude Desktop, Claude Code) can call.
2. **iscreami consuming MCP** — an AI-powered ingredient enrichment tool that calls the Anthropic API to estimate missing composition, PAC, and POD values for an ingredient.

## Architecture

### Transport

Streamable HTTP transport at `/mcp`, mounted inside the existing FastAPI app. No separate process or container. Works with Claude Desktop, Claude Code, and any MCP-compatible HTTP client.

### New Files

| File | Purpose |
|------|---------|
| `backend/api/mcp_server.py` | Defines all MCP tools using the official `mcp` Python SDK; uses `SessionLocal()` for DB access |
| `backend/api/services/ai.py` | Anthropic SDK calls for ingredient enrichment; pure function — no DB access, no FastAPI deps |
| `backend/tests/test_mcp.py` | Unit tests for MCP tools using MagicMock fixtures |
| `backend/tests/test_ai.py` | Unit tests for AI enrichment with mocked Anthropic client |

### Modified Files

| File | Change |
|------|--------|
| `backend/api/app.py` | Mount MCP server at `/mcp` — **before** the SPA catch-all `/{full_path:path}` route |
| `backend/api/settings.py` | Add optional `ANTHROPIC_API_KEY: str \| None = None` |
| `backend/pyproject.toml` | Add `mcp` and `anthropic` dependencies |
| `.env.example` | Document `ANTHROPIC_API_KEY` as optional |

### DB Session in MCP Tools

MCP tool functions are **not** FastAPI route handlers — `Depends(get_db)` is never invoked for them. Tools must manage sessions directly:

```python
from api.db import SessionLocal

def some_tool(...):
    db = SessionLocal()
    try:
        ...
    finally:
        db.close()
```

This mirrors the existing `get_db()` implementation in `db.py`. Do not import `DbSession` or use `Depends` in `mcp_server.py`.

### Mount Ordering

The SPA catch-all `@app.get("/{full_path:path}")` at the end of `app.py` will swallow `/mcp` requests if the MCP server is not mounted first. The mount must appear **before** that handler:

```python
app.mount("/mcp", mcp_app)           # must precede the SPA catch-all
app.get("/{full_path:path}")(serve_spa)
```

### Key Constraint

The MCP server does not reimplement any business logic. All tools delegate to the existing service layer (`calculate()`, `pac.py`, etc.) and existing Pydantic schemas for serialization.

### `services/ai.py` Classification

`ai.py` follows the "no DB / no FastAPI" services rule but introduces I/O (Anthropic API calls). It is a new sub-category within `services/`: side-effectful but framework-free. `enrich_ingredient()` returns a dict; the MCP tool layer is responsible for persisting the result.

## MCP Tools

### ID Types

- Ingredient, recipe, and profile IDs are `uuid.UUID` — pass as UUID strings
- `category_id` on ingredients is `int`
- `RecipeIngredientOut.id` (line item) is `int`

### Ingredients

| Tool | Signature | Returns |
|------|-----------|---------|
| `list_ingredient_categories` | `()` | List of `{id: int, name: str, slug: str}` |
| `list_ingredients` | `(search?: str, category_id?: int)` | `PaginatedIngredients` |
| `get_ingredient` | `(id: UUID)` | `IngredientOut` (includes computed `pac`, `pod`, `total_solids_pct`) |
| `create_ingredient` | All fields from `IngredientCreate` schema — see below | `IngredientOut` |
| `update_ingredient` | `(id: UUID)` + any subset of `IngredientUpdate` fields | `IngredientOut` |
| `delete_ingredient` | `(id: UUID)` | Confirmation or error if referenced by a recipe (block delete) |
| `enrich_ingredient` | `(id: UUID)` | `IngredientOut` + `{"fields_updated": [...]}` |

**`create_ingredient` / `update_ingredient` fields** (from `IngredientBase` in `schemas.py`):

Core: `name`, `description`, `category_id`, `source`, `source_id`

Composition per 100g: `water_pct`, `total_fat_pct`, `saturated_fat_pct`, `trans_fat_pct`, `protein_pct`, `carbohydrate_pct`, `fiber_pct`, `total_sugar_pct`, `energy_kj_per_100g`, `alcohol_pct`, `sodium_mg` (mg/100g — **not a percentage**)

Sugar breakdown: `sucrose_pct`, `glucose_pct`, `fructose_pct`, `lactose_pct`, `maltose_pct`, `galactose_pct`

Dairy: `milk_fat_pct`, `msnf_pct`

Chocolate: `cocoa_butter_pct`, `cocoa_solids_pct`

Other: `stabilizer_pct`, `emulsifier_pct`, `pac_override`, `pod_override`, `aliases`

**Delete behaviour:** Block deletion if any `RecipeIngredient` row references this ingredient. Return a clear error naming the recipes that reference it.

### Recipes

| Tool | Signature | Returns |
|------|-----------|---------|
| `list_recipes` | `(search?: str, page?: int, page_size?: int)` | `PaginatedRecipes` |
| `get_recipe` | `(id: UUID)` | `RecipeOut` including `ingredients[]` with full `IngredientOut` nested |
| `create_recipe` | `(name, description?, recipe_type?, target_profile_id?: UUID, ingredients?: [{ingredient_id, weight_grams, sort_order?}])` | `RecipeOut` |
| `update_recipe` | `(id: UUID, name?, description?, recipe_type?, target_profile_id?)` | `RecipeOut` |
| `delete_recipe` | `(id: UUID)` | Confirmation |
| `add_recipe_ingredient` | `(recipe_id: UUID, ingredient_id: UUID, weight_grams: float)` | Updated `RecipeOut`. If `ingredient_id` already exists in the recipe, **add a new line item** (duplicate is valid — e.g. two milks). |
| `update_recipe_ingredient` | `(recipe_id: UUID, item_id: int, weight_grams: float)` | Updated `RecipeOut`. Error if `item_id` does not belong to `recipe_id`. |
| `remove_recipe_ingredient` | `(recipe_id: UUID, item_id: int)` | Updated `RecipeOut`. Error if `item_id` does not belong to `recipe_id`. |

### Profiles

| Tool | Signature | Returns |
|------|-----------|---------|
| `list_profiles` | `()` | List of `TargetProfileOut` |
| `get_profile` | `(id: UUID)` | `TargetProfileOut` |
| `create_profile` | `(name)` + optional range fields — see below | `TargetProfileOut` |
| `update_profile` | `(id: UUID)` + any subset of profile fields | `TargetProfileOut` |
| `delete_profile` | `(id: UUID)` | Confirmation. **Null out** `target_profile_id` on any recipes that reference this profile before deleting. |

**Profile range fields** (all `float | None`, from `TargetProfileBase`):
`serving_temp_min/max`, `sweetness_min/max`, `total_solids_min/max`, `total_fat_min/max`, `milk_fat_min/max`, `sugar_min/max`, `alcohol_min/max`, `msnf_min/max`, `stabilizer_min/max`, `emulsifier_min/max`

### Calculate

| Tool | Signature | Returns |
|------|-----------|---------|
| `calculate_recipe` | `(recipe_id: UUID, serving_size_g?: float = 66.0)` | `CalculateResponse` — see below |

**`CalculateResponse` fields:**
- `composition` — `total_weight_g`, `water_pct`, `total_solids_pct`, `total_fat_pct`, `saturated_fat_pct`, `trans_fat_pct`, `milk_fat_pct`, `msnf_pct`, `total_sugar_pct`, `protein_pct`, `carbohydrate_pct`, `fiber_pct`, `alcohol_pct`, `stabilizer_pct`, `emulsifier_pct`
- `pac` — `pac_mix` (per 100g total mix), `pac_water` (per 100g free water; `null` if no free water)
- `freezing` — `freezing_point_c`, `serving_temperature_c`, `curve: [{temperature_c, frozen_water_pct}]`
- `sweetness` — `pod`, `sweetener_breakdown: [{ingredient_name, weight_g, pct_of_sweeteners}]`
- `nutrition` — `per_100g`, `per_serving`, `serving_size_g`
- `target_comparison` — `null` if no profile set; otherwise `[{metric, value, target_min, target_max, status}]` where `status` is a **3-value enum**: `"in_range"` / `"below"` / `"above"` (not binary pass/fail)

**Edge cases:**
- Empty recipe (no ingredients): return a clear error — `calculate_recipe` requires at least one ingredient
- `pac_water` is `null` when there is no free water (100% solids) — this is normal, not an error
- `target_comparison` is `null` when `target_profile_id` is `null` — return the rest of the response normally

**Total: 21 tools**

## AI Enrichment (`services/ai.py`)

### Function Signature

```python
def enrich_ingredient(ingredient: Ingredient) -> dict[str, float | None]:
    """
    Calls Anthropic API to estimate missing composition/PAC/POD values.
    Returns a dict of {field_name: estimated_value} for fields that are None.
    Raises ValueError if ANTHROPIC_API_KEY is not configured.
    Raises anthropic.APIError (and subclasses) on API failures.
    """
```

### Behaviour

1. Collect all composition fields that are **`None`** (not `0` — zero is a valid user-set value)
2. Build a structured prompt with the ingredient name and any already-known non-`None` values as context
3. Call Anthropic API with tool use to receive a structured response
4. Return only the fields that were `None` — never include fields that already have a value

**The `0` vs `None` distinction is load-bearing.** `lactose_pct = 0` for a non-dairy ingredient is a deliberate user decision. Only treat `None` as "not yet set."

### Fields Estimated

Composition: `water_pct`, `total_fat_pct`, `total_sugar_pct`, `protein_pct`, `carbohydrate_pct`, `sodium_mg`, `lactose_pct`, `sucrose_pct`, `glucose_pct`, `fructose_pct`, `milk_fat_pct`, `msnf_pct`

PAC/POD overrides: **only for ingredients that are known non-standard solutes** (e.g. polyols, salts, alcohol) where the override meaningfully differs from what the composition-based calculation would produce. The prompt must explicitly instruct the model not to set `pac_override`/`pod_override` for standard ingredients — doing so would permanently override correct calculated values with estimates.

### Prompt Requirements

The prompt must communicate:
- `sodium_mg` is milligrams per 100g — **not a percentage** (the model will default to grams or percent without this constraint)
- All `_pct` fields are grams per 100g of ingredient (i.e. %)
- The PAC factor table (from AGENTS.md) so override estimates are anchored, not hallucinated
- The known non-zero values already set, to constrain the estimates

### Model

`claude-sonnet-4-6` — better numeric reasoning than Haiku for PAC/POD estimation; cost difference is negligible at single-ingredient interactive volumes.

### Error Handling

```python
# In mcp_server.py enrich_ingredient tool:
try:
    estimates = ai.enrich_ingredient(ingredient)
except ValueError as e:
    return {"error": str(e)}  # missing API key
except anthropic.APIError as e:
    return {"error": f"Anthropic API error: {e}"}  # covers RateLimitError, APITimeoutError, etc.
```

Raw exceptions must not reach the MCP client as unhandled errors.

### Configuration

```
# .env.example addition
# Anthropic API key — required only for the enrich_ingredient MCP tool
ANTHROPIC_API_KEY=sk-ant-...
```

`settings.py`: `anthropic_api_key: str | None = None`

## Testing

### `tests/test_mcp.py`

Uses the existing `MagicMock` pattern. For each tool:
- Happy path: correct return shape
- Not-found: returns appropriate error message
- Delegation: verifies the tool calls the correct service function (not reimplementing logic)
- Session management: `SessionLocal()` is called and `.close()` is called in all paths (including error paths)

### `tests/test_ai.py`

Mocks the `anthropic.Anthropic` client. Covers:
- `None` fields are filled in; `0` fields are left untouched
- Existing non-`None` values are not returned in the result dict
- Missing `ANTHROPIC_API_KEY` raises `ValueError` with a clear message
- `anthropic.APIError` propagates correctly

### Integration

A small number of end-to-end tool calls (list categories, create ingredient, create recipe, calculate) run against the existing real-SQLite integration test DB. No Anthropic API calls in integration tests.

## Dependencies

```toml
# backend/pyproject.toml additions
"mcp>=1.0",
"anthropic>=0.40",
```

## Non-Goals

- MCP Resources (URI-based reads like `ingredient://123`) — tools are sufficient and simpler
- Authentication on the `/mcp` endpoint — consistent with the rest of the app (single-user, private network)
- Streaming tool responses — not needed for this use case
- Recipe export/import tools — `RecipeExportOut`/`ImportErrorResponse` exist but are out of scope for this feature
- Bulk ingredient enrichment — if added later, use the Anthropic Batches API (async, 50% cheaper) rather than a loop
