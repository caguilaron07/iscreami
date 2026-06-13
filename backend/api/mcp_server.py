"""MCP server for iscreami — exposes recipe calculator tools to AI clients."""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import anthropic
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from api.db import SessionLocal
from api.models import (
    Ingredient,
    IngredientAlias,
    IngredientCategory,
    Recipe,
    RecipeIngredient,
    TargetProfile,
)
from api.schemas import (
    IngredientCreate,
    IngredientOut,
    IngredientUpdate,
    PaginatedIngredients,
    PaginatedRecipes,
    RecipeCreate,
    RecipeOut,
    RecipeUpdate,
    TargetProfileCreate,
    TargetProfileOut,
    TargetProfileUpdate,
)
from api.services import ai

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
def get_ingredient(id: str) -> dict:
    """Get a single ingredient by UUID. Includes computed pac, pod, total_solids_pct."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        ing = db.get(
            Ingredient,
            pk,
            options=[joinedload(Ingredient.category), selectinload(Ingredient.aliases)],
        )
        if not ing:
            return {"error": f"Ingredient {id} not found"}
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
def update_ingredient(id: str, data: IngredientUpdate) -> dict:
    """Update an existing ingredient. Only provided fields are changed."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        ing = db.get(
            Ingredient,
            pk,
            options=[joinedload(Ingredient.category), selectinload(Ingredient.aliases)],
        )
        if not ing:
            return {"error": f"Ingredient {id} not found"}
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
def delete_ingredient(id: str) -> dict:
    """Delete an ingredient. Returns error if referenced by any recipe."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        ing = db.get(Ingredient, pk)
        if not ing:
            return {"error": f"Ingredient {id} not found"}
        refs = db.scalars(
            select(Recipe)
            .join(RecipeIngredient, RecipeIngredient.recipe_id == Recipe.id)
            .where(RecipeIngredient.ingredient_id == ing.id)
            .distinct()
        ).all()
        if refs:
            names = ", ".join(f'"{r.name}"' for r in refs)
            return {"error": f"Ingredient is used in recipe(s): {names} and cannot be deleted"}
        db.delete(ing)
        db.commit()
        return {"deleted": id}


@mcp.tool()
def enrich_ingredient(id: str) -> dict:
    """Use AI to estimate missing composition/PAC/POD values and persist them.

    Only fills fields that are currently None — never overwrites non-None values.
    Requires ANTHROPIC_API_KEY to be set in .env.
    Returns {"fields_updated": [...], "ingredient": {...}}.
    """
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        ing = db.get(
            Ingredient,
            pk,
            options=[joinedload(Ingredient.category), selectinload(Ingredient.aliases)],
        )
        if not ing:
            return {"error": f"Ingredient {id} not found"}

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


@mcp.tool()
def list_profiles() -> list[dict]:
    """List all target profiles (Gelato, Ice Cream, Sorbet, etc.)."""
    with _db() as db:
        profiles = db.scalars(
            select(TargetProfile).order_by(TargetProfile.name)
        ).all()
        return [TargetProfileOut.model_validate(p).model_dump(mode="json") for p in profiles]


@mcp.tool()
def get_profile(id: str) -> dict:
    """Get a single target profile by UUID."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        prof = db.get(TargetProfile, pk)
        if not prof:
            return {"error": f"Profile {id} not found"}
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
def update_profile(id: str, data: TargetProfileUpdate) -> dict:
    """Update a target profile. Only provided fields are changed."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        prof = db.get(TargetProfile, pk)
        if not prof:
            return {"error": f"Profile {id} not found"}
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(prof, key, value)
        db.commit()
        db.refresh(prof)
        return TargetProfileOut.model_validate(prof).model_dump(mode="json")


@mcp.tool()
def delete_profile(id: str) -> dict:
    """Delete a target profile. Nulls out target_profile_id on any recipes that reference it."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        prof = db.get(TargetProfile, pk, options=[joinedload(TargetProfile.recipes)])
        if not prof:
            return {"error": f"Profile {id} not found"}
        for recipe in prof.recipes:
            recipe.target_profile_id = None
        db.delete(prof)
        db.commit()
        return {"deleted": id}


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
def get_recipe(id: str) -> dict:
    """Get a recipe by UUID including all ingredients and their full details."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        recipe = _load_recipe(db, pk)
        if not recipe:
            return {"error": f"Recipe {id} not found"}
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
def update_recipe(id: str, data: RecipeUpdate) -> dict:
    """Update recipe metadata (name, description, recipe_type, target_profile_id).
    To change ingredients use add/update/remove_recipe_ingredient tools."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        recipe = _load_recipe(db, pk)
        if not recipe:
            return {"error": f"Recipe {id} not found"}
        payload = data.model_dump(exclude_unset=True)
        payload.pop("ingredients", None)
        for field, value in payload.items():
            setattr(recipe, field, value)
        db.commit()
        loaded = _load_recipe(db, recipe.id)
        return RecipeOut.model_validate(loaded).model_dump(mode="json")


@mcp.tool()
def delete_recipe(id: str) -> dict:
    """Delete a recipe and all its ingredient line items."""
    try:
        pk = uuid.UUID(id)
    except ValueError:
        return {"error": f"Invalid UUID: {id}"}
    with _db() as db:
        recipe = db.get(Recipe, pk)
        if not recipe:
            return {"error": f"Recipe {id} not found"}
        db.delete(recipe)
        db.commit()
        return {"deleted": id}
