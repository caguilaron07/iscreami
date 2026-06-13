# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**iscreami** is an open-source ice cream recipe calculator. Users build recipes ingredient-by-ingredient and get real-time feedback on freezing properties (PAC), sweetness (POD), composition, and nutrition.

Stack: FastAPI + SQLAlchemy (sync) + PostgreSQL backend; React 19 + TypeScript + Vite + Tailwind CSS v4 + DaisyUI v5 frontend. Served as a single Docker container on port 8000.

## Development Commands

### Backend

```bash
cd backend
uv sync                                          # Install deps
uv run uvicorn api.app:app --reload              # Dev server on :8000
uv run pytest tests/ -v                          # All tests
uv run pytest tests/ -m "not integration"        # Unit tests only (fast)
uv run pytest tests/test_pac.py -v               # Single test file
uv run ruff check .                              # Lint
uv run ruff format .                             # Format
uv run mypy .                                    # Type check
```

### Frontend

```bash
cd frontend
pnpm install
pnpm dev                  # Dev server on :5173 (proxies /api/* → :8000)
pnpm tsc --noEmit         # Type check
pnpm lint                 # ESLint
pnpm build                # Production build
```

### Database

```bash
cd backend
uv run alembic upgrade head                                      # Apply migrations
uv run alembic revision --autogenerate -m "description"          # New migration after model change
uv run python -m cli.main seed                                   # Seed categories, profiles, ~40 ingredients
```

## Architecture

```
backend/
  api/
    app.py          # FastAPI entry point — load_dotenv() MUST be first import
    db.py           # SQLAlchemy sync engine, SessionLocal, DbSession, NOT_FOUND_RESPONSE
    models.py       # SQLAlchemy ORM models
    schemas.py      # Pydantic v2 schemas
    routes/         # FastAPI routers (ingredients, recipes, profiles, calculate)
    services/       # Pure calculation functions — pac.py, sweetness.py, freezing.py, calculator.py
    settings.py     # Pydantic settings
  cli/
    main.py         # Typer CLI
    seed.py         # Seed data
    importers/      # NZFCDB (.FT) and USDA (CSV/JSON) importers
  alembic/          # DB migrations
  tests/            # pytest; MagicMock for ingredients/profiles (not real DB except integration tests)

frontend/
  src/
    api.ts          # All API calls go through request<T>() — never fetch directly in components
    types.ts        # TypeScript interfaces mirroring backend schemas
    hooks/          # useRecipeCalculator.ts — debounced calculate + state; useTheme.ts; ToastProvider
    components/     # Pure display components receiving props; no direct API calls
    lib/            # formatting.ts, tooltips.ts, validation.ts
```

## Key Conventions

### Backend

- `load_dotenv()` must be called before any import that reads `os.environ` (e.g. `db.py` reads `DATABASE_URL` at import time)
- SQLAlchemy is **synchronous** — no `async` sessions
- Routes use `DbSession = Annotated[Session, Depends(get_db)]` and `NOT_FOUND_RESPONSE` from `api.db`
- `Out` schemas need `model_config = ConfigDict(from_attributes=True)` (Pydantic v2)
- Use `StrEnum` (not `str, Enum`) for enum schemas
- Services in `services/` are pure functions — no DB access, no FastAPI deps
- `calculate()` in `calculator.py` takes `list[tuple[Ingredient, float]]` (ingredient, weight_g)
- Composition fields: `_pct` = grams per 100g of ingredient; `sodium_mg` = mg/100g (multiply by 0.001 for grams)
- `pac_override` / `pod_override` on an ingredient always takes precedence over calculated values

### Frontend

- API calls only through functions in `api.ts`
- Components are pure display — state and data fetching live in hooks or `App.tsx`
- Tailwind CSS v4 with `@tailwindcss/vite` (no `tailwind.config.js`)
- DaisyUI v5 — prefer semantic tokens (`bg-base-100/200/300`, `text-base-content`, `btn`, `badge`, etc.) over raw Tailwind grey classes
- Dark mode: set both `class="dark"` on `<html>` (Tailwind) AND `data-theme="dark"/"light"` (DaisyUI) — `useTheme.ts` handles both
- React Router v7 routes: `/` HomePage, `/calculator` CalculatorView, `/ingredients` IngredientsView, `/recipes` RecipesView
- TanStack React Query for server state; Recharts for charts; Zod for validation

## Science Reference

**PAC** (freezing point depression, relative to sucrose=100): Glucose/Fructose/Allulose: 190, Erythritol: 280, Glycerin: 372, NaCl: 585, Ethanol: 743. Sodium: `sodium_g × 2.58 × 585 / 100`. Displayed as `pac_mix` (per 100g total) and `pac_water` (per 100g free water).

**POD** (relative sweetness vs. sucrose=1.0): Fructose: 1.7, Glucose: 0.75, Lactose: 0.16, Erythritol: 0.65.

When adding a new solute, update both `PAC_FACTORS` in `services/pac.py` and `POD_FACTORS` in `services/sweetness.py`.

## Environment

`.env` file at repo root (see `.env.example`):
- `DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/iscreami` (required)
- `CORS_ORIGINS` — comma-separated origins (default: all)
- `SERVING_SIZE_G` — nutrition serving size in grams (default: 66)
