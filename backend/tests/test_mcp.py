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
    from sqlalchemy.exc import IntegrityError
    from api.mcp_server import delete_ingredient

    mock_db = _mock_db()
    mock_db.get.return_value = MagicMock()
    mock_db.commit.side_effect = IntegrityError("", {}, Exception())

    with patch("api.mcp_server.SessionLocal", return_value=mock_db):
        result = delete_ingredient(_uuid_str())

    assert "error" in result
    assert "recipe" in result["error"].lower()
