# MCP Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add MCP support to iscreami — an embedded MCP server exposing 21 tools for full CRUD on ingredients, recipes, and profiles plus recipe calculation, plus an AI enrichment service that estimates missing ingredient values using the Anthropic API.

**Architecture:** Streamable HTTP MCP server mounted at `/mcp` inside the existing FastAPI app using `FastMCP` from the official `mcp` SDK. MCP tools use `SessionLocal()` directly because FastAPI's `Depends(get_db)` DI cycle does not run for MCP handlers. AI enrichment in `services/ai.py` is a pure I/O function; the `enrich_ingredient` MCP tool handles DB persistence of its results.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy (sync), `mcp>=1.0` (FastMCP), `anthropic>=0.40`, pytest, MagicMock

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/pyproject.toml` | Modify | Add `mcp` and `anthropic` deps |
| `backend/api/settings.py` | Modify | Add optional `ANTHROPIC_API_KEY` |
| `backend/.env.example` | Modify | Document new env var |
| `backend/api/services/ai.py` | Create | `enrich_ingredient()` pure function |
| `backend/tests/test_ai.py` | Create | Unit tests for `ai.py` |
| `backend/api/mcp_server.py` | Create | All 21 MCP tools, `_db()` session helper |
| `backend/api/app.py` | Modify | Mount MCP server at `/mcp` before SPA catch-all |
| `backend/tests/test_mcp.py` | Create | Unit tests for all MCP tools |

---

## Task 1: Dependencies and Config

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/api/settings.py`
- Modify: `backend/.env.example`

- [ ] **Step 1: Add deps to pyproject.toml**

In `backend/pyproject.toml`, add to the `dependencies` list:

```toml
dependencies = [
    "fastapi>=0.115,<1.0",
    "sqlalchemy>=2.0,<3.0",
    "psycopg[binary]>=3.1",
    "alembic>=1.18.4",
    "pydantic-settings>=2.0",
    "python-multipart>=0.0.27",
    "uvicorn>=0.34",
    "typer>=0.24.1",
    "ijson>=3.5.0",
    "mcp>=1.0",
    "anthropic>=0.40",
]
```

- [ ] **Step 2: Add ANTHROPIC_API_KEY to settings.py**

In `backend/api/settings.py`, add one field to the `Settings` class:

```python
anthropic_api_key: str | None = None
```

- [ ] **Step 3: Document in .env.example**

Append to `backend/.env.example`:

```
# Anthropic API key — required only for the enrich_ingredient MCP tool
ANTHROPIC_API_KEY=
```

- [ ] **Step 4: Install deps**

```bash
cd backend
uv sync
```

Expected: resolves and installs `mcp` and `anthropic` packages without errors.

- [ ] **Step 5: Verify imports work**

```bash
uv run python -c "import mcp; import anthropic; print('ok')"
```

Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/api/settings.py backend/.env.example
git commit -m "feat: add mcp and anthropic dependencies"
```

---

## Task 2: AI Enrichment Service

**Files:**
- Create: `backend/api/services/ai.py`
- Create: `backend/tests/test_ai.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_ai.py`:

```python
"""Tests for AI ingredient enrichment service."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from api.services import ai


def _make_ingredient(**kwargs):
    """Create a mock ingredient with all composition fields None by default."""
    m = MagicMock()
    defaults = {f: None for f in ai._ESTIMABLE_FIELDS}
    defaults["pac_override"] = None
    defaults["pod_override"] = None
    defaults["name"] = "Test Ingredient"
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def test_enrich_raises_without_api_key(monkeypatch):
    monkeypatch.setattr("api.services.ai.settings.anthropic_api_key", None)
    ing = _make_ingredient(name="Whole milk")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ai.enrich_ingredient(ing)


def test_enrich_returns_empty_when_no_missing_fields(monkeypatch):
    monkeypatch.setattr("api.services.ai.settings.anthropic_api_key", "sk-test")
    # All fields already set — nothing to estimate, no API call should be made
    ing = _make_ingredient(
        name="Whole milk",
        water_pct=87.5,
        total_fat_pct=3.5,
        total_sugar_pct=4.7,
        protein_pct=3.2,
        carbohydrate_pct=4.8,
        sodium_mg=44.0,
        lactose_pct=4.7,
        sucrose_pct=0.0,
        glucose_pct=0.0,
        fructose_pct=0.0,
        milk_fat_pct=3.5,
        msnf_pct=8.5,
    )
    result = ai.enrich_ingredient(ing)
    assert result == {}


def test_enrich_fills_none_fields_not_zero(monkeypatch):
    monkeypatch.setattr("api.services.ai.settings.anthropic_api_key", "sk-test")
    # lactose_pct=0.0 (explicit zero, user-set), water_pct=None (missing)
    ing = _make_ingredient(name="Oat milk", lactose_pct=0.0, total_fat_pct=1.5)

    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.input = {
        "water_pct": 90.0,
        "lactose_pct": 3.0,   # should be ignored — 0.0 is not None
        "total_fat_pct": 5.0,  # should be ignored — already set to 1.5
        "sodium_mg": 52.0,
    }

    mock_response = MagicMock()
    mock_response.content = [mock_block]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("api.services.ai.anthropic.Anthropic", return_value=mock_client):
        result = ai.enrich_ingredient(ing)

    assert result.get("water_pct") == 90.0
    assert result.get("sodium_mg") == 52.0
    assert "lactose_pct" not in result  # 0.0 is not None — must not be overwritten
    assert "total_fat_pct" not in result  # 1.5 already set — must not be overwritten


def test_enrich_propagates_api_error(monkeypatch):
    import anthropic as ant

    monkeypatch.setattr("api.services.ai.settings.anthropic_api_key", "sk-test")
    ing = _make_ingredient(name="Cream")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = ant.APIError(
        message="rate limited", request=MagicMock(), body=None
    )

    with patch("api.services.ai.anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(ant.APIError):
            ai.enrich_ingredient(ing)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend
uv run pytest tests/test_ai.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — `api.services.ai` does not exist yet.

- [ ] **Step 3: Create `backend/api/services/ai.py`**

```python
"""AI-powered ingredient enrichment using the Anthropic API."""
from __future__ import annotations

import anthropic

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

_ENRICHMENT_TOOL: dict = {
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
                "description": "Sodium — MILLIGRAMS per 100g (NOT grams, NOT percent). Typical range 1–2000 mg/100g.",
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


def enrich_ingredient(ingredient: Ingredient) -> dict[str, float]:
    """Estimate None-valued composition fields via Anthropic API.

    Only fills fields that are currently None — never touches fields set to 0 or
    any other non-None value.

    Returns a dict of {field_name: estimated_value} to apply to the ingredient.

    Raises:
        ValueError: ANTHROPIC_API_KEY not configured.
        anthropic.APIError: API call failed (covers RateLimitError, APITimeoutError, etc.).
    """
    all_estimable = _ESTIMABLE_FIELDS + ["pac_override", "pod_override"]
    missing = [f for f in all_estimable if getattr(ingredient, f, None) is None]
    if not missing:
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
        tool_choice={"type": "any"},
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
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd backend
uv run pytest tests/test_ai.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Run ruff**

```bash
uv run ruff check api/services/ai.py
```

Expected: no issues.

- [ ] **Step 6: Commit**

```bash
git add backend/api/services/ai.py backend/tests/test_ai.py
git commit -m "feat: add AI ingredient enrichment service"
```

---

## Task 3: MCP Server Scaffold and Mount

**Files:**
- Create: `backend/api/mcp_server.py`
- Modify: `backend/api/app.py` (add mount between line 60 and line 64)

- [ ] **Step 1: Create `backend/api/mcp_server.py` scaffold**

```python
"""MCP server for iscreami — exposes recipe calculator tools to AI clients."""
from __future__ import annotations

from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

from api.db import SessionLocal

mcp = FastMCP("iscreami")


@contextmanager
def _db():
    """Open a SQLAlchemy session for use in MCP tool functions.

    MCP tools are not FastAPI route handlers, so Depends(get_db) never fires.
    This replicates the same open/close lifecycle as get_db() in db.py.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 2: Mount in app.py**

In `backend/api/app.py`, add the import and mount after the existing `/api/v1` mount (line 60) and before the assets/SPA mount (line 64):

```python
# After:  app.mount("/api/v1", api)
# Before: app.mount("/assets", ...)

from api.mcp_server import mcp  # add this import at the top of the file with other imports

# add this line after app.mount("/api/v1", api):
app.mount("/mcp", mcp.streamable_http_app())
```

> **Note for implementer:** `streamable_http_app()` is the MCP SDK 1.x method for streamable HTTP transport. If the installed version uses a different method name, check `dir(mcp)` or the SDK CHANGELOG.

- [ ] **Step 3: Verify server starts**

```bash
cd backend
uv run uvicorn api.app:app --reload &
sleep 3
curl -s http://localhost:8000/mcp | head -20
kill %1
```

Expected: the `/mcp` endpoint responds (HTTP 200 or 405) rather than serving the SPA HTML — confirming the mount is in place before the catch-all.

- [ ] **Step 4: Run existing tests to confirm nothing broke**

```bash
uv run pytest tests/ -v --ignore=tests/test_mcp.py --ignore=tests/test_ai.py
```

Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/mcp_server.py backend/api/app.py
git commit -m "feat: mount MCP server scaffold at /mcp"
```

---

## Task 4: Ingredient Tools

**Files:**
- Modify: `backend/api/mcp_server.py` (add 6 tools)
- Create: `backend/tests/test_mcp.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_mcp.py`:

```python
"""Unit tests for MCP server tools."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch


# --- Helpers ---

def _mock_db():
    """Return a mock that acts as the SQLAlchemy session."""
    return MagicMock()


def _uuid_str():
    return str(uuid.uuid4())


# --- list_ingredient_categories ---

def test_list_ingredient_categories_returns_list():
    from api.mcp_server import list_ingredient_categories

    cat = MagicMock(id=1, name="Dairy", slug="dairy")
    mock_db = _mock_db()
    mock_db.scalars.return_value.all.return_value = [cat]

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = list_ingredient_categories()

    assert isinstance(result, list)
    assert result[0]["id"] == 1
    assert result[0]["name"] == "Dairy"
    assert result[0]["slug"] == "dairy"


def test_list_ingredient_categories_empty():
    from api.mcp_server import list_ingredient_categories

    mock_db = _mock_db()
    mock_db.scalars.return_value.all.return_value = []

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = list_ingredient_categories()

    assert result == []


# --- list_ingredients ---

def test_list_ingredients_returns_paginated():
    from api.mcp_server import list_ingredients

    mock_db = _mock_db()
    mock_db.scalar.return_value = 0
    mock_db.scalars.return_value.unique.return_value.all.return_value = []

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = list_ingredients()

    assert "total" in result
    assert "items" in result
    assert result["total"] == 0


# --- get_ingredient ---

def test_get_ingredient_not_found():
    from api.mcp_server import get_ingredient

    mock_db = _mock_db()
    mock_db.get.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = get_ingredient(_uuid_str())

    assert "error" in result


# --- delete_ingredient ---

def test_delete_ingredient_not_found():
    from api.mcp_server import delete_ingredient

    mock_db = _mock_db()
    mock_db.get.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = delete_ingredient(_uuid_str())

    assert "error" in result


def test_delete_ingredient_referenced_by_recipe():
    from sqlalchemy.exc import IntegrityError
    from api.mcp_server import delete_ingredient

    mock_db = _mock_db()
    mock_db.get.return_value = MagicMock()
    mock_db.commit.side_effect = IntegrityError("", {}, Exception())

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = delete_ingredient(_uuid_str())

    assert "error" in result
    assert "recipe" in result["error"].lower()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend
uv run pytest tests/test_mcp.py -v
```

Expected: `ImportError` — tools not defined yet.

- [ ] **Step 3: Add ingredient tools to `mcp_server.py`**

Append to `backend/api/mcp_server.py`:

```python
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from api.models import Ingredient, IngredientAlias, IngredientCategory
from api.schemas import IngredientCategoryOut, IngredientCreate, IngredientOut, IngredientUpdate, PaginatedIngredients


@mcp.tool()
def list_ingredient_categories() -> list[dict]:
    """List all ingredient categories with id, name, and slug."""
    with _db() as db:
        cats = db.scalars(
            select(IngredientCategory).order_by(IngredientCategory.name)
        ).all()
        return [{"id": c.id, "name": c.name, "slug": c.slug} for c in cats]


@mcp.tool()
def list_ingredients(
    search: str | None = None,
    category_id: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    """List ingredients with optional search and category filter. Returns {total, items}."""
    with _db() as db:
        stmt = select(Ingredient).options(
            joinedload(Ingredient.category),
            selectinload(Ingredient.aliases),
        )
        if search:
            alias_subq = select(IngredientAlias.ingredient_id).where(
                IngredientAlias.alias.ilike(f"%{search}%")
            )
            stmt = stmt.where(
                or_(Ingredient.name.ilike(f"%{search}%"), Ingredient.id.in_(alias_subq))
            )
        if category_id is not None:
            stmt = stmt.where(Ingredient.category_id == category_id)

        stmt = stmt.order_by(Ingredient.name)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(count_stmt) or 0
        items = list(db.scalars(stmt.offset(offset).limit(limit)).unique().all())
        return PaginatedIngredients(total=total, items=items).model_dump(mode="json")  # type: ignore[arg-type]


@mcp.tool()
def get_ingredient(ingredient_id: str) -> dict:
    """Get a single ingredient by UUID. Includes computed pac, pod, total_solids_pct."""
    with _db() as db:
        ing = db.get(
            Ingredient,
            uuid.UUID(ingredient_id),
            options=[joinedload(Ingredient.category), selectinload(Ingredient.aliases)],
        )
        if not ing:
            return {"error": f"Ingredient {ingredient_id} not found"}
        return IngredientOut.model_validate(ing).model_dump(mode="json")


@mcp.tool()
def create_ingredient(data: IngredientCreate) -> dict:
    """Create a new ingredient. Pass all IngredientCreate fields."""
    with _db() as db:
        payload = data.model_dump()
        alias_names: list[str] = payload.pop("aliases", [])
        ing = Ingredient(**payload)
        ing.aliases = [IngredientAlias(alias=a) for a in alias_names]
        db.add(ing)
        db.commit()
        db.refresh(ing, attribute_names=["category", "aliases"])
        return IngredientOut.model_validate(ing).model_dump(mode="json")


@mcp.tool()
def update_ingredient(ingredient_id: str, data: IngredientUpdate) -> dict:
    """Update an existing ingredient. Only provided fields are changed."""
    with _db() as db:
        ing = db.get(
            Ingredient,
            uuid.UUID(ingredient_id),
            options=[joinedload(Ingredient.category), selectinload(Ingredient.aliases)],
        )
        if not ing:
            return {"error": f"Ingredient {ingredient_id} not found"}
        payload = data.model_dump(exclude_unset=True)
        alias_names: list[str] | None = payload.pop("aliases", None)
        for key, value in payload.items():
            setattr(ing, key, value)
        if alias_names is not None:
            ing.aliases = [IngredientAlias(alias=a) for a in alias_names]
        db.commit()
        db.refresh(ing, attribute_names=["category", "aliases"])
        return IngredientOut.model_validate(ing).model_dump(mode="json")


@mcp.tool()
def delete_ingredient(ingredient_id: str) -> dict:
    """Delete an ingredient. Returns error if referenced by any recipe."""
    with _db() as db:
        ing = db.get(Ingredient, uuid.UUID(ingredient_id))
        if not ing:
            return {"error": f"Ingredient {ingredient_id} not found"}
        db.delete(ing)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return {"error": "Ingredient is used in one or more recipes and cannot be deleted"}
        return {"deleted": ingredient_id}
```

- [ ] **Step 4: Run tests**

```bash
cd backend
uv run pytest tests/test_mcp.py -v
```

Expected: all tests in this file pass.

- [ ] **Step 5: Run ruff**

```bash
uv run ruff check api/mcp_server.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/api/mcp_server.py backend/tests/test_mcp.py
git commit -m "feat: add ingredient MCP tools (list, get, create, update, delete)"
```

---

## Task 5: Enrich Ingredient Tool

**Files:**
- Modify: `backend/api/mcp_server.py` (add 1 tool)
- Modify: `backend/tests/test_mcp.py` (add tests)

- [ ] **Step 1: Add tests for enrich_ingredient**

Append to `backend/tests/test_mcp.py`:

```python
# --- enrich_ingredient ---

def test_enrich_ingredient_not_found():
    from api.mcp_server import enrich_ingredient

    mock_db = _mock_db()
    mock_db.get.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = enrich_ingredient(_uuid_str())

    assert "error" in result


def test_enrich_ingredient_missing_api_key():
    from api.mcp_server import enrich_ingredient

    ing = MagicMock()
    mock_db = _mock_db()
    mock_db.get.return_value = ing

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        with patch("api.mcp_server.ai.enrich_ingredient", side_effect=ValueError("ANTHROPIC_API_KEY is not configured")):
            result = enrich_ingredient(_uuid_str())

    assert "error" in result
    assert "ANTHROPIC_API_KEY" in result["error"]


def test_enrich_ingredient_api_error():
    import anthropic as ant
    from api.mcp_server import enrich_ingredient

    ing = MagicMock()
    mock_db = _mock_db()
    mock_db.get.return_value = ing

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        with patch(
            "api.mcp_server.ai.enrich_ingredient",
            side_effect=ant.APIError(message="rate limited", request=MagicMock(), body=None),
        ):
            result = enrich_ingredient(_uuid_str())

    assert "error" in result


def test_enrich_ingredient_updates_fields():
    from api.mcp_server import enrich_ingredient

    ing = MagicMock()
    mock_db = _mock_db()
    mock_db.get.return_value = ing

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        with patch("api.mcp_server.ai.enrich_ingredient", return_value={"water_pct": 88.0, "sodium_mg": 44.0}):
            with patch("api.mcp_server.IngredientOut.model_validate") as mock_validate:
                mock_validate.return_value.model_dump.return_value = {"id": "abc", "name": "Milk"}
                result = enrich_ingredient(_uuid_str())

    assert result["fields_updated"] == ["water_pct", "sodium_mg"]
    assert result["ingredient"]["name"] == "Milk"
    assert ing.water_pct == 88.0
    assert ing.sodium_mg == 44.0
```

- [ ] **Step 2: Run to confirm new tests fail**

```bash
cd backend
uv run pytest tests/test_mcp.py::test_enrich_ingredient_not_found -v
```

Expected: `ImportError` or `AttributeError` — tool not defined yet.

- [ ] **Step 3: Add enrich_ingredient to mcp_server.py**

Append to `backend/api/mcp_server.py` (also add `import anthropic` and `from api.services import ai` at the top imports):

```python
import anthropic

from api.services import ai
```

Then append the tool:

```python
@mcp.tool()
def enrich_ingredient(ingredient_id: str) -> dict:
    """Use AI to estimate missing composition/PAC/POD values and persist them.

    Only fills fields that are currently None — never overwrites non-None values.
    Requires ANTHROPIC_API_KEY to be set in .env.
    Returns {"fields_updated": [...], "ingredient": {...}}.
    """
    with _db() as db:
        ing = db.get(
            Ingredient,
            uuid.UUID(ingredient_id),
            options=[joinedload(Ingredient.category), selectinload(Ingredient.aliases)],
        )
        if not ing:
            return {"error": f"Ingredient {ingredient_id} not found"}

        try:
            estimates = ai.enrich_ingredient(ing)
        except ValueError as exc:
            return {"error": str(exc)}
        except anthropic.APIError as exc:
            return {"error": f"Anthropic API error: {exc}"}

        for field, value in estimates.items():
            setattr(ing, field, value)

        if estimates:
            db.commit()
            db.refresh(ing, attribute_names=["category", "aliases"])

        return {
            "fields_updated": list(estimates.keys()),
            "ingredient": IngredientOut.model_validate(ing).model_dump(mode="json"),
        }
```

- [ ] **Step 4: Run tests**

```bash
cd backend
uv run pytest tests/test_mcp.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/mcp_server.py backend/tests/test_mcp.py
git commit -m "feat: add enrich_ingredient MCP tool"
```

---

## Task 6: Profile Tools

**Files:**
- Modify: `backend/api/mcp_server.py` (add 5 tools)
- Modify: `backend/tests/test_mcp.py` (add tests)

- [ ] **Step 1: Add profile tests**

Append to `backend/tests/test_mcp.py`:

```python
# --- Profile tools ---

def test_list_profiles_returns_list():
    from api.mcp_server import list_profiles

    profile = MagicMock()
    profile.id = uuid.uuid4()
    profile.name = "Gelato"
    mock_db = _mock_db()
    mock_db.scalars.return_value.all.return_value = [profile]

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        with patch("api.mcp_server.TargetProfileOut.model_validate") as mv:
            mv.return_value.model_dump.return_value = {"id": str(profile.id), "name": "Gelato"}
            result = list_profiles()

    assert len(result) == 1
    assert result[0]["name"] == "Gelato"


def test_get_profile_not_found():
    from api.mcp_server import get_profile

    mock_db = _mock_db()
    mock_db.get.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = get_profile(_uuid_str())

    assert "error" in result


def test_delete_profile_nulls_recipe_references():
    from api.mcp_server import delete_profile

    prof = MagicMock()
    prof.recipes = [MagicMock(target_profile_id=uuid.uuid4()), MagicMock(target_profile_id=uuid.uuid4())]
    mock_db = _mock_db()
    mock_db.get.return_value = prof

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = delete_profile(_uuid_str())

    for recipe in prof.recipes:
        assert recipe.target_profile_id is None
    assert "deleted" in result
```

- [ ] **Step 2: Run to confirm new tests fail**

```bash
uv run pytest tests/test_mcp.py::test_list_profiles_returns_list -v
```

Expected: `ImportError`.

- [ ] **Step 3: Add profile tools to mcp_server.py**

Add to the imports at the top of `mcp_server.py`:

```python
from api.models import TargetProfile
from api.schemas import TargetProfileCreate, TargetProfileOut, TargetProfileUpdate
```

Then append the tools:

```python
@mcp.tool()
def list_profiles() -> list[dict]:
    """List all target profiles (Gelato, Ice Cream, Sorbet, etc.)."""
    with _db() as db:
        profiles = db.scalars(
            select(TargetProfile).order_by(TargetProfile.name)
        ).all()
        return [TargetProfileOut.model_validate(p).model_dump(mode="json") for p in profiles]


@mcp.tool()
def get_profile(profile_id: str) -> dict:
    """Get a single target profile by UUID."""
    with _db() as db:
        prof = db.get(TargetProfile, uuid.UUID(profile_id))
        if not prof:
            return {"error": f"Profile {profile_id} not found"}
        return TargetProfileOut.model_validate(prof).model_dump(mode="json")


@mcp.tool()
def create_profile(data: TargetProfileCreate) -> dict:
    """Create a new target profile. All range fields are optional."""
    with _db() as db:
        prof = TargetProfile(**data.model_dump())
        db.add(prof)
        db.commit()
        db.refresh(prof)
        return TargetProfileOut.model_validate(prof).model_dump(mode="json")


@mcp.tool()
def update_profile(profile_id: str, data: TargetProfileUpdate) -> dict:
    """Update a target profile. Only provided fields are changed."""
    with _db() as db:
        prof = db.get(TargetProfile, uuid.UUID(profile_id))
        if not prof:
            return {"error": f"Profile {profile_id} not found"}
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(prof, key, value)
        db.commit()
        db.refresh(prof)
        return TargetProfileOut.model_validate(prof).model_dump(mode="json")


@mcp.tool()
def delete_profile(profile_id: str) -> dict:
    """Delete a target profile. Nulls out target_profile_id on any recipes that reference it."""
    with _db() as db:
        prof = db.get(TargetProfile, uuid.UUID(profile_id), options=[joinedload(TargetProfile.recipes)])
        if not prof:
            return {"error": f"Profile {profile_id} not found"}
        for recipe in prof.recipes:
            recipe.target_profile_id = None
        db.delete(prof)
        db.commit()
        return {"deleted": profile_id}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_mcp.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/mcp_server.py backend/tests/test_mcp.py
git commit -m "feat: add profile MCP tools (list, get, create, update, delete)"
```

---

## Task 7: Recipe CRUD Tools

**Files:**
- Modify: `backend/api/mcp_server.py` (add 5 tools)
- Modify: `backend/tests/test_mcp.py` (add tests)

- [ ] **Step 1: Add recipe CRUD tests**

Append to `backend/tests/test_mcp.py`:

```python
# --- Recipe CRUD tools ---

def test_list_recipes_returns_paginated():
    from api.mcp_server import list_recipes

    mock_db = _mock_db()
    mock_db.scalar.return_value = 0
    mock_db.scalars.return_value.unique.return_value.all.return_value = []

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = list_recipes()

    assert "total" in result
    assert result["total"] == 0
    assert result["items"] == []


def test_get_recipe_not_found():
    from api.mcp_server import get_recipe

    mock_db = _mock_db()
    mock_db.scalars.return_value.unique.return_value.first.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = get_recipe(_uuid_str())

    assert "error" in result


def test_delete_recipe_not_found():
    from api.mcp_server import delete_recipe

    mock_db = _mock_db()
    mock_db.get.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = delete_recipe(_uuid_str())

    assert "error" in result
```

- [ ] **Step 2: Run to confirm new tests fail**

```bash
uv run pytest tests/test_mcp.py::test_list_recipes_returns_paginated -v
```

Expected: `ImportError`.

- [ ] **Step 3: Add recipe CRUD tools to mcp_server.py**

Add to the imports at the top of `mcp_server.py`:

```python
from api.models import Recipe, RecipeIngredient
from api.schemas import PaginatedRecipes, RecipeCreate, RecipeOut, RecipeUpdate
```

Then append the tools:

```python
def _load_recipe(db, recipe_id: uuid.UUID) -> Recipe | None:
    """Load a recipe with all relationships eagerly loaded."""
    stmt = (
        select(Recipe)
        .where(Recipe.id == recipe_id)
        .options(
            joinedload(Recipe.target_profile),
            joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient),
        )
    )
    return db.scalars(stmt).unique().first()


@mcp.tool()
def list_recipes(
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """List recipes with optional name search. Returns {total, items}."""
    with _db() as db:
        stmt = select(Recipe).options(
            joinedload(Recipe.target_profile),
            joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient),
        )
        if search:
            stmt = stmt.where(Recipe.name.ilike(f"%{search}%"))
        stmt = stmt.order_by(Recipe.updated_at.desc())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(count_stmt) or 0
        offset = (page - 1) * page_size
        items = list(db.scalars(stmt.offset(offset).limit(page_size)).unique().all())
        return PaginatedRecipes(total=total, items=items).model_dump(mode="json")  # type: ignore[arg-type]


@mcp.tool()
def get_recipe(recipe_id: str) -> dict:
    """Get a recipe by UUID including all ingredients and their full details."""
    with _db() as db:
        recipe = _load_recipe(db, uuid.UUID(recipe_id))
        if not recipe:
            return {"error": f"Recipe {recipe_id} not found"}
        return RecipeOut.model_validate(recipe).model_dump(mode="json")


@mcp.tool()
def create_recipe(data: RecipeCreate) -> dict:
    """Create a new recipe. ingredients list is optional."""
    with _db() as db:
        recipe = Recipe(
            name=data.name,
            description=data.description,
            recipe_type=data.recipe_type,
            target_profile_id=data.target_profile_id,
        )
        db.add(recipe)
        db.flush()
        for inp in data.ingredients:
            db.add(RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_id=inp.ingredient_id,
                weight_grams=inp.weight_grams,
                sort_order=inp.sort_order,
            ))
        db.commit()
        loaded = _load_recipe(db, recipe.id)
        return RecipeOut.model_validate(loaded).model_dump(mode="json")


@mcp.tool()
def update_recipe(recipe_id: str, data: RecipeUpdate) -> dict:
    """Update recipe metadata (name, description, recipe_type, target_profile_id).
    To change ingredients use add/update/remove_recipe_ingredient tools."""
    with _db() as db:
        recipe = _load_recipe(db, uuid.UUID(recipe_id))
        if not recipe:
            return {"error": f"Recipe {recipe_id} not found"}
        payload = data.model_dump(exclude_unset=True)
        payload.pop("ingredients", None)  # line-item changes go through dedicated tools
        for field, value in payload.items():
            setattr(recipe, field, value)
        db.commit()
        loaded = _load_recipe(db, recipe.id)
        return RecipeOut.model_validate(loaded).model_dump(mode="json")


@mcp.tool()
def delete_recipe(recipe_id: str) -> dict:
    """Delete a recipe and all its ingredient line items."""
    with _db() as db:
        recipe = db.get(Recipe, uuid.UUID(recipe_id))
        if not recipe:
            return {"error": f"Recipe {recipe_id} not found"}
        db.delete(recipe)
        db.commit()
        return {"deleted": recipe_id}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_mcp.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/mcp_server.py backend/tests/test_mcp.py
git commit -m "feat: add recipe CRUD MCP tools (list, get, create, update, delete)"
```

---

## Task 8: Recipe Ingredient Line Item Tools

**Files:**
- Modify: `backend/api/mcp_server.py` (add 3 tools)
- Modify: `backend/tests/test_mcp.py` (add tests)

> **Note:** `RecipeIngredient` has `UniqueConstraint("recipe_id", "ingredient_id")` at the DB level — the same ingredient can only appear once per recipe. `add_recipe_ingredient` must catch `IntegrityError` and return a conflict error.

- [ ] **Step 1: Add line item tests**

Append to `backend/tests/test_mcp.py`:

```python
# --- Recipe ingredient line item tools ---

def test_add_recipe_ingredient_recipe_not_found():
    from api.mcp_server import add_recipe_ingredient

    mock_db = _mock_db()
    mock_db.scalars.return_value.unique.return_value.first.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = add_recipe_ingredient(_uuid_str(), _uuid_str(), 100.0)

    assert "error" in result


def test_add_recipe_ingredient_duplicate_returns_conflict():
    from sqlalchemy.exc import IntegrityError
    from api.mcp_server import add_recipe_ingredient

    recipe = MagicMock()
    mock_db = _mock_db()
    mock_db.scalars.return_value.unique.return_value.first.return_value = recipe
    mock_db.flush.side_effect = IntegrityError("", {}, Exception())

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = add_recipe_ingredient(_uuid_str(), _uuid_str(), 100.0)

    assert "error" in result
    assert "already" in result["error"].lower()


def test_update_recipe_ingredient_wrong_recipe():
    from api.mcp_server import update_recipe_ingredient

    item = MagicMock()
    item.recipe_id = uuid.uuid4()  # different from what we'll pass
    mock_db = _mock_db()
    mock_db.get.return_value = item

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = update_recipe_ingredient(_uuid_str(), 99, 150.0)

    assert "error" in result


def test_remove_recipe_ingredient_not_found():
    from api.mcp_server import remove_recipe_ingredient

    mock_db = _mock_db()
    mock_db.get.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = remove_recipe_ingredient(_uuid_str(), 99)

    assert "error" in result
```

- [ ] **Step 2: Run to confirm new tests fail**

```bash
uv run pytest tests/test_mcp.py::test_add_recipe_ingredient_recipe_not_found -v
```

Expected: `ImportError`.

- [ ] **Step 3: Add line item tools to mcp_server.py**

Append to `backend/api/mcp_server.py`:

```python
@mcp.tool()
def add_recipe_ingredient(recipe_id: str, ingredient_id: str, weight_grams: float) -> dict:
    """Add an ingredient to a recipe.

    Each ingredient can appear at most once per recipe (DB constraint).
    Returns an error if the ingredient is already in the recipe.
    """
    with _db() as db:
        recipe = _load_recipe(db, uuid.UUID(recipe_id))
        if not recipe:
            return {"error": f"Recipe {recipe_id} not found"}

        sort_order = len(recipe.ingredients)
        db.add(RecipeIngredient(
            recipe_id=recipe.id,
            ingredient_id=uuid.UUID(ingredient_id),
            weight_grams=weight_grams,
            sort_order=sort_order,
        ))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"error": "Ingredient is already in this recipe"}

        db.commit()
        loaded = _load_recipe(db, recipe.id)
        return RecipeOut.model_validate(loaded).model_dump(mode="json")


@mcp.tool()
def update_recipe_ingredient(recipe_id: str, item_id: int, weight_grams: float) -> dict:
    """Update the weight of a recipe ingredient line item.

    item_id is the integer id from RecipeIngredientOut.id.
    Returns an error if item_id does not belong to the given recipe.
    """
    rid = uuid.UUID(recipe_id)
    with _db() as db:
        item = db.get(RecipeIngredient, item_id)
        if not item or item.recipe_id != rid:
            return {"error": f"Recipe ingredient {item_id} not found in recipe {recipe_id}"}
        item.weight_grams = weight_grams
        db.commit()
        loaded = _load_recipe(db, rid)
        return RecipeOut.model_validate(loaded).model_dump(mode="json")


@mcp.tool()
def remove_recipe_ingredient(recipe_id: str, item_id: int) -> dict:
    """Remove an ingredient line item from a recipe.

    item_id is the integer id from RecipeIngredientOut.id.
    Returns an error if item_id does not belong to the given recipe.
    """
    rid = uuid.UUID(recipe_id)
    with _db() as db:
        item = db.get(RecipeIngredient, item_id)
        if not item or item.recipe_id != rid:
            return {"error": f"Recipe ingredient {item_id} not found in recipe {recipe_id}"}
        db.delete(item)
        db.commit()
        loaded = _load_recipe(db, rid)
        return RecipeOut.model_validate(loaded).model_dump(mode="json")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_mcp.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/api/mcp_server.py backend/tests/test_mcp.py
git commit -m "feat: add recipe ingredient line item MCP tools"
```

---

## Task 9: Calculate Tool and Integration Tests

**Files:**
- Modify: `backend/api/mcp_server.py` (add 1 tool)
- Modify: `backend/tests/test_mcp.py` (add unit + integration tests)

- [ ] **Step 1: Add calculate tests**

Append to `backend/tests/test_mcp.py`:

```python
# --- calculate_recipe ---

def test_calculate_recipe_not_found():
    from api.mcp_server import calculate_recipe

    mock_db = _mock_db()
    mock_db.scalars.return_value.unique.return_value.first.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = calculate_recipe(_uuid_str())

    assert "error" in result


def test_calculate_recipe_empty_returns_error():
    from api.mcp_server import calculate_recipe

    recipe = MagicMock()
    recipe.ingredients = []
    mock_db = _mock_db()
    mock_db.scalars.return_value.unique.return_value.first.return_value = recipe

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = calculate_recipe(_uuid_str())

    assert "error" in result
    assert "ingredient" in result["error"].lower()


def test_calculate_recipe_no_profile_returns_null_comparison():
    from api.mcp_server import calculate_recipe
    from api.schemas import CalculateResponse

    ri = MagicMock()
    ri.ingredient = MagicMock()
    ri.weight_grams = 100.0
    recipe = MagicMock()
    recipe.ingredients = [ri]
    recipe.target_profile = None

    mock_calc_result = MagicMock(spec=CalculateResponse)
    mock_calc_result.model_dump.return_value = {"target_comparison": None, "pac": {}}

    mock_db = _mock_db()
    mock_db.scalars.return_value.unique.return_value.first.return_value = recipe

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        with patch("api.mcp_server.calculate", return_value=mock_calc_result):
            result = calculate_recipe(_uuid_str())

    assert result["target_comparison"] is None


# --- Integration tests (real SQLite DB) ---

import pytest

@pytest.mark.integration
def test_integration_list_categories_and_create_ingredient(test_db):
    from api.mcp_server import list_ingredient_categories, create_ingredient
    from api.models import IngredientCategory
    from api.schemas import IngredientCreate

    cat = IngredientCategory(name="Dairy", slug="dairy")
    test_db.add(cat)
    test_db.commit()

    with patch("api.mcp_server.SessionLocal", return_value=test_db):
        categories = list_ingredient_categories()
        assert any(c["slug"] == "dairy" for c in categories)

        data = IngredientCreate(
            name="Whole Milk",
            category_id=cat.id,
            water_pct=87.5,
            total_fat_pct=3.5,
            total_sugar_pct=4.7,
            sodium_mg=44.0,
        )
        result = create_ingredient(data)
        assert result["name"] == "Whole Milk"
        assert result["category"]["slug"] == "dairy"


@pytest.mark.integration
def test_integration_create_and_calculate_recipe(test_db):
    from api.mcp_server import create_recipe, calculate_recipe
    from api.models import IngredientCategory, Ingredient
    from api.schemas import RecipeCreate, RecipeIngredientInput

    cat = IngredientCategory(name="Dairy", slug="dairy")
    test_db.add(cat)
    test_db.flush()

    milk = Ingredient(
        name="Whole Milk",
        category_id=cat.id,
        water_pct=87.5,
        total_fat_pct=3.5,
        total_sugar_pct=4.7,
        sodium_mg=44.0,
    )
    test_db.add(milk)
    test_db.commit()

    with patch("api.mcp_server.SessionLocal", return_value=test_db):
        recipe_data = RecipeCreate(
            name="Simple Milk Base",
            ingredients=[RecipeIngredientInput(ingredient_id=milk.id, weight_grams=1000.0)],
        )
        recipe = create_recipe(recipe_data)
        recipe_id = recipe["id"]

        result = calculate_recipe(recipe_id)
        assert "pac" in result
        assert "freezing" in result
        assert "sweetness" in result
        assert result["target_comparison"] is None
```

- [ ] **Step 2: Run to confirm new tests fail**

```bash
uv run pytest tests/test_mcp.py::test_calculate_recipe_not_found -v
```

Expected: `ImportError`.

- [ ] **Step 3: Add calculate_recipe tool to mcp_server.py**

Add import at the top of `mcp_server.py`:

```python
from api.schemas import CalculateResponse
from api.services.calculator import calculate
```

Then append the tool:

```python
@mcp.tool()
def calculate_recipe(recipe_id: str, serving_size_g: float = 66.0) -> dict:
    """Calculate all metrics for a saved recipe.

    Returns composition, pac (mix + water), freezing curve, sweetness (POD),
    nutrition per_100g and per_serving, and target_comparison (null if no profile set).
    target_comparison status is a 3-value enum: 'in_range', 'below', or 'above'.

    serving_size_g defaults to 66.0g (configurable per-call).
    """
    with _db() as db:
        recipe = _load_recipe(db, uuid.UUID(recipe_id))
        if not recipe:
            return {"error": f"Recipe {recipe_id} not found"}
        if not recipe.ingredients:
            return {"error": "Recipe has no ingredients — add at least one before calculating"}

        items = [(ri.ingredient, ri.weight_grams) for ri in recipe.ingredients]
        result = calculate(
            items=items,
            target_profile=recipe.target_profile,
            serving_size_g=serving_size_g,
        )
        return result.model_dump(mode="json")
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass (unit + integration).

- [ ] **Step 5: Run ruff on the full mcp_server.py**

```bash
uv run ruff check api/mcp_server.py api/services/ai.py
```

Expected: no issues.

- [ ] **Step 6: Final commit**

```bash
git add backend/api/mcp_server.py backend/tests/test_mcp.py
git commit -m "feat: add calculate_recipe MCP tool and integration tests"
```

---

## Post-Implementation

After all tasks complete, verify the full server starts and the tool count is correct:

```bash
cd backend
uv run uvicorn api.app:app --reload &
sleep 3
# List tools via MCP protocol (requires an MCP client, or check logs for registration)
kill %1
uv run pytest tests/ -v
```

Expected: 21 tools registered, all 4 test files pass.
