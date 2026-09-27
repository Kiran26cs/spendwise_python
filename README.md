# SpendWise

A small, production-style personal expense tracker built to learn Python end to end.
Backend: Python 3.12+ / FastAPI / SQLAlchemy 2.0 / PostgreSQL. Frontend: Angular (Phase 7).

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the full design, phases and the
Python concept map.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`pip install uv`)
- Docker (PostgreSQL for local dev and integration tests)

## Setup

```bash
docker compose up -d postgres        # start PostgreSQL
cd backend
cp .env.example .env                 # adjust if needed
uv sync                              # create .venv and install dependencies
cd ..
backend/.venv/Scripts/pre-commit install   # git hooks (macOS/Linux: backend/.venv/bin/pre-commit)
```

## Run

```bash
cd backend
uv run uvicorn spendwise.main:app --reload
```

- Health: http://localhost:8000/health
- API docs: http://localhost:8000/docs

## Quality gates

```bash
cd backend
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov=spendwise --cov-report=term-missing
```

## Environment variables

All settings live in `backend/src/spendwise/core/config.py` and can be set in `backend/.env`.
See `backend/.env.example` for the full list.
