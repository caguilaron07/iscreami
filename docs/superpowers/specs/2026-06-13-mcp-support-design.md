# MCP Support Design

**Date:** 2026-06-13
**Status:** Approved

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
| `backend/api/mcp_server.py` | Defines all MCP tools using the official `mcp` Python SDK; imports existing service layer and `DbSession` |
| `backend/api/services/ai.py` | Anthropic SDK calls for ingredient enrichment; pure function, no DB access |
| `backend/tests/test_mcp.py` | Unit tests for MCP tools using MagicMock fixtures |
| `backend/tests/test_ai.py` | Unit tests for AI enrichment with mocked Anthropic client |

### Modified Files

| File | Change |
|------|--------|
| `backend/api/app.py` | Mount MCP server at `/mcp` alongside existing routes |
| `backend/api/settings.py` | Add optional `ANTHROPIC_API_KEY` field |
| `backend/pyproject.toml` | Add `mcp` and `anthropic` dependencies |
| `.env.example` | Document `ANTHROPIC_API_KEY` as optional |

### Key Constraint

The MCP server does not reimplement any business logic. All tools delegate to the existing service layer (`calculate()`, `pac.py`, etc.) and use the same `DbSession` dependency already used by routes.

## MCP Tools

### Ingredients

| Tool | Signature | Returns |
|------|-----------|---------|
| `list_ingredients` | `(search?, category_id?)` | List of ingredients |
| `get_ingredient` | `(id)` | Full ingredient detail |
| `create_ingredient` | `(name, category_id, water_pct, fat_pct, sugar_pct, lactose_pct, other_solids_pct, sodium_mg, pac_override?, pod_override?)` | Created ingredient |
| `update_ingredient` | `(id, ...all fields optional)` | Updated ingredient |
| `delete_ingredient` | `(id)` | Confirmation message |
| `enrich_ingredient` | `(id)` | Updated ingredient + list of fields changed |

### Recipes

| Tool | Signature | Returns |
|------|-----------|---------|
| `list_recipes` | `()` | All recipes with target profile name |
| `get_recipe` | `(id)` | Recipe + ingredients + latest calculation results |
| `create_recipe` | `(name, target_profile_id?)` | Created recipe |
| `update_recipe` | `(id, name?, target_profile_id?)` | Updated recipe |
| `delete_recipe` | `(id)` | Confirmation message |
| `add_recipe_ingredient` | `(recipe_id, ingredient_id, weight_g)` | Updated recipe |
| `update_recipe_ingredient` | `(recipe_id, item_id, weight_g)` | Updated recipe |
| `remove_recipe_ingredient` | `(recipe_id, item_id)` | Updated recipe |

### Profiles

| Tool | Signature | Returns |
|------|-----------|---------|
| `list_profiles` | `()` | All target profiles with range fields |
| `get_profile` | `(id)` | Full profile detail |
| `create_profile` | `(name, ...range fields)` | Created profile |
| `update_profile` | `(id, ...fields optional)` | Updated profile |
| `delete_profile` | `(id)` | Confirmation message |

### Calculate

| Tool | Signature | Returns |
|------|-----------|---------|
| `calculate_recipe` | `(recipe_id)` | PAC (mix + water), POD, composition breakdown, nutrition per 100g and per serving, freezing curve points, target profile comparison with pass/fail per range |

**Total: 20 tools**

## AI Enrichment (`services/ai.py`)

### Behaviour

`enrich_ingredient(ingredient: Ingredient) -> dict` is a pure function that:

1. Builds a prompt with the ingredient name and any already-known non-zero values as context
2. Calls Anthropic API using tool use to get a structured response
3. Returns a dict of estimated values for fields that are currently `0` or `None`

**Never overwrites user-set values** — only fills fields that are `0` or `None`.

### Fields Estimated

- Composition: `water_pct`, `fat_pct`, `sugar_pct`, `lactose_pct`, `other_solids_pct`, `sodium_mg`
- `pac_override` if the ingredient is a non-standard solute with a known freezing point depression factor
- `pod_override` if the ingredient has a known relative sweetness different from its sugar content

### Model

`claude-haiku-4-5` — lowest cost, sufficient for structured data extraction from ingredient names.

### Error Handling

If `ANTHROPIC_API_KEY` is not set, `enrich_ingredient` raises a descriptive `ValueError`. The `enrich_ingredient` MCP tool catches this and returns a clear error message to the client rather than a 500.

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

### `tests/test_ai.py`

Mocks the `anthropic.Anthropic` client. Covers:
- Fields are filled in when currently zero/None
- Existing non-zero values are not overwritten
- Missing `ANTHROPIC_API_KEY` raises `ValueError` with a clear message

### Integration

A small number of end-to-end tool calls (list ingredients, create recipe, calculate) run against the existing real-SQLite integration test DB. No Anthropic API calls in integration tests.

## Dependencies

```toml
# backend/pyproject.toml additions
"mcp>=1.0",
"anthropic>=0.40",
```

## Non-Goals

- MCP Resources (URI-based reads like `ingredient://123`) — tools are sufficient and simpler to implement
- Authentication on the `/mcp` endpoint — consistent with the rest of the app (single-user, private network)
- Streaming tool responses — not needed for this use case
