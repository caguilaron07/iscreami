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

    with patch("api.services.ai.anthropic.Anthropic", return_value=mock_client), pytest.raises(ant.APIError):
        ai.enrich_ingredient(ing)
