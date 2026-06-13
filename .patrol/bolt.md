# Bolt — Performance Findings

## 2026-06-11
- Fixed N+1 query for `target_profile` in recipe endpoints (`list_recipes`, `_load_recipe`, `export_all_recipes`) — added `joinedload(Recipe.target_profile)` to all recipe queries to avoid lazy-loading the target profile once per recipe row during serialization. Opened PR #11.

## 2026-06-13
- Replaced joinedload with selectinload for Recipe.ingredients in list_recipes and export_all_recipes to avoid cartesian product on shared ingredients — opened PR #14
