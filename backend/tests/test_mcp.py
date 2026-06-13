"""Unit tests for MCP server tools."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch


def _mock_db():
    """Return a mock that acts as the SQLAlchemy session."""
    return MagicMock()


def _uuid_str():
    return str(uuid.uuid4())


# --- list_ingredient_categories ---

def test_list_ingredient_categories_returns_list():
    from api.mcp_server import list_ingredient_categories

    cat = MagicMock(id=1, slug="dairy")
    cat.name = "Dairy"
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
    from api.mcp_server import delete_ingredient

    mock_recipe = MagicMock()
    mock_recipe.name = "Vanilla Bean"
    mock_db = _mock_db()
    mock_db.get.return_value = MagicMock()
    mock_db.scalars.return_value.all.return_value = [mock_recipe]

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = delete_ingredient(_uuid_str())

    assert "error" in result
    assert "recipe" in result["error"].lower()
    assert "Vanilla Bean" in result["error"]


# --- get_ingredient (happy path) ---

def test_get_ingredient_found():
    from api.mcp_server import get_ingredient

    ingredient_id = _uuid_str()
    mock_db = _mock_db()
    mock_db.get.return_value = MagicMock()

    with patch("api.mcp_server.SessionLocal", return_value=mock_db), \
         patch("api.mcp_server.IngredientOut") as mock_out:
        mock_out.model_validate.return_value.model_dump.return_value = {"id": ingredient_id, "name": "Milk"}
        result = get_ingredient(ingredient_id)

    assert result["name"] == "Milk"


# --- create_ingredient ---

def test_create_ingredient_returns_out():
    from api.mcp_server import create_ingredient
    from api.schemas import IngredientCreate

    data = IngredientCreate(name="Test Milk")
    mock_db = _mock_db()

    with patch("api.mcp_server.SessionLocal", return_value=mock_db), \
         patch("api.mcp_server.IngredientOut") as mock_out:
        mock_out.model_validate.return_value.model_dump.return_value = {"id": _uuid_str(), "name": "Test Milk"}
        result = create_ingredient(data)

    assert result["name"] == "Test Milk"
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()


# --- update_ingredient ---

def test_update_ingredient_not_found():
    from api.mcp_server import update_ingredient
    from api.schemas import IngredientUpdate

    mock_db = _mock_db()
    mock_db.get.return_value = None

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = update_ingredient(_uuid_str(), IngredientUpdate())

    assert "error" in result


def test_update_ingredient_found():
    from api.mcp_server import update_ingredient
    from api.schemas import IngredientUpdate

    ingredient_id = _uuid_str()
    mock_db = _mock_db()
    mock_db.get.return_value = MagicMock()

    with patch("api.mcp_server.SessionLocal", return_value=mock_db), \
         patch("api.mcp_server.IngredientOut") as mock_out:
        mock_out.model_validate.return_value.model_dump.return_value = {"id": ingredient_id, "name": "Updated"}
        result = update_ingredient(ingredient_id, IngredientUpdate(name="Updated"))

    assert result["name"] == "Updated"
    mock_db.commit.assert_called_once()


# --- delete_ingredient (happy path) ---

def test_delete_ingredient_success():
    from api.mcp_server import delete_ingredient

    ingredient_id = _uuid_str()
    mock_db = _mock_db()
    mock_db.get.return_value = MagicMock()
    mock_db.scalars.return_value.all.return_value = []  # no referencing recipes

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = delete_ingredient(ingredient_id)

    assert result == {"deleted": ingredient_id}
    mock_db.delete.assert_called_once()
    mock_db.commit.assert_called_once()


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

    with (
        patch("api.mcp_server.SessionLocal", return_value=mock_db),
        patch("api.mcp_server.ai.enrich_ingredient", side_effect=ValueError("ANTHROPIC_API_KEY is not configured")),
    ):
        result = enrich_ingredient(_uuid_str())

    assert "error" in result
    assert "ANTHROPIC_API_KEY" in result["error"]


def test_enrich_ingredient_api_error():
    import anthropic as ant

    from api.mcp_server import enrich_ingredient

    ing = MagicMock()
    mock_db = _mock_db()
    mock_db.get.return_value = ing

    with (
        patch("api.mcp_server.SessionLocal", return_value=mock_db),
        patch(
            "api.mcp_server.ai.enrich_ingredient",
            side_effect=ant.APIError(message="rate limited", request=MagicMock(), body=None),
        ),
    ):
        result = enrich_ingredient(_uuid_str())

    assert "error" in result


def test_enrich_ingredient_updates_fields():
    from api.mcp_server import enrich_ingredient

    ing = MagicMock()
    mock_db = _mock_db()
    mock_db.get.return_value = ing

    with (
        patch("api.mcp_server.SessionLocal", return_value=mock_db),
        patch("api.mcp_server.ai.enrich_ingredient", return_value={"water_pct": 88.0, "sodium_mg": 44.0}),
        patch("api.mcp_server.IngredientOut.model_validate") as mock_validate,
    ):
        mock_validate.return_value.model_dump.return_value = {"id": "abc", "name": "Milk"}
        result = enrich_ingredient(_uuid_str())

    assert result["fields_updated"] == ["water_pct", "sodium_mg"]
    assert result["ingredient"]["name"] == "Milk"
    assert ing.water_pct == 88.0
    assert ing.sodium_mg == 44.0


# --- Profile tools ---

def test_list_profiles_returns_list():
    from api.mcp_server import list_profiles

    profile = MagicMock()
    profile.id = uuid.uuid4()
    profile.name = "Gelato"
    mock_db = _mock_db()
    mock_db.scalars.return_value.all.return_value = [profile]

    with (
        patch("api.mcp_server.SessionLocal", return_value=mock_db),
        patch("api.mcp_server.TargetProfileOut.model_validate") as mv,
    ):
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
    recipe.ingredients = []
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
