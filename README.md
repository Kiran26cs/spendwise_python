# SpendWise

A small, production-style **personal expense tracker** built to learn Python end to end, from
basics to advanced concepts, using industry-standard tools and design patterns.

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2.0, PostgreSQL, Alembic, Pydantic v2
- **Frontend:** Angular (Phase 7, not built yet)

The full design, phase checklist and the **Python concept map** (which file demonstrates which
concept) are in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#11-python-concept-map-learning-guide).

## Features

| | |
|---|---|
| Categories | CRUD with an optional monthly budget (null = no budget, 0 allowed) |
| Expenses | CRUD, filters (date range, category), pagination |
| Multi-currency | USD, EUR, GBP, SGD, AED converted to INR with live rates ([Frankfurter](https://frankfurter.dev)) |
| Monthly report | Spend per category, budget used %, over-budget flags; export as CSV or JSON |
| CSV import | Streamed, validated row by row; valid rows import even when others fail |
| Audit trail | Every create/update/delete/import is logged in the same DB transaction |
| Observability | JSON logs; every request and audit row carries an `X-Correlation-ID` |

Money is exact end to end: JSON numbers on the wire, `Decimal` in Python, `NUMERIC` in
PostgreSQL. `float` is never used for money arithmetic.

## Quick start (Docker)

Requires Docker Desktop.

```bash
docker compose up -d --build        # PostgreSQL + API; migrations run automatically
```

- API docs (Swagger UI): http://localhost:8000/docs
- Health: http://localhost:8000/health

Load sample data:

```bash
docker compose exec api python scripts/seed.py --categories --expenses 50 --seed 42
curl -F "file=@backend/sample_data/expenses_sample.csv" http://localhost:8000/api/v1/imports/expenses
```

Stop with `docker compose down` (add `-v` to also delete the database volume).

## Local development

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) (`pip install uv`) and Docker.

```bash
docker compose up -d postgres            # database only, on host port 5433
cd backend
cp .env.example .env                     # adjust if needed
uv sync                                  # create .venv with all dependencies
uv run alembic upgrade head              # create/upgrade the schema
uv run uvicorn spendwise.main:app --reload
```

Git hooks (ruff + mypy on every commit), from the repository root:

```bash
backend/.venv/Scripts/pre-commit install   # macOS/Linux: backend/.venv/bin/pre-commit
```

Handy commands (from `backend/`):

| Task | Command |
|---|---|
| Seed data | `uv run python scripts/seed.py --categories --expenses 50 --seed 42` |
| Wipe all data | `uv run python scripts/seed.py --reset` |
| New migration | `uv run alembic revision --autogenerate -m "describe change"` |
| Readable logs | set `LOG_JSON=false` in `.env` |

## Tests and quality gates

Integration tests start a throwaway PostgreSQL container (testcontainers), so Docker must be
running. Each test runs in a transaction that is rolled back afterwards.

```bash
cd backend
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov=spendwise --cov-report=term-missing
```

`uv run pytest tests/unit` runs the fast suite without Docker.

## Configuration

All settings live in `backend/src/spendwise/core/config.py` and are read from environment
variables or `backend/.env` (see `backend/.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://spendwise:spendwise@localhost:5433/spendwise` | SQLAlchemy URL |
| `ENVIRONMENT` | `local` | `local`, `test` (static FX rates, no network) or `prod` |
| `LOG_LEVEL` | `INFO` | `DEBUG` also logs execution times |
| `LOG_JSON` | `true` | `false` for human-readable local logs |
| `FX_API_URL` | `https://api.frankfurter.dev/v1` | Exchange-rate API |
| `FX_TIMEOUT_SECONDS` | `5` | Per-request timeout (3 attempts with backoff) |
| `FX_CACHE_TTL_SECONDS` | `3600` | How long a rate is cached |
| `CORS_ORIGINS` | `["http://localhost:4200"]` | Allowed browser origins (JSON list) |
| `MAX_UPLOAD_MB` | `5` | CSV import size limit (larger files get 413) |
| `APP_NAME`, `APP_VERSION`, `DEBUG` | | Shown in `/docs` and `/health` |
| `TZ` (Docker only) | `Asia/Kolkata` | Defines "today" for the no-future-dates rule |

## Architecture

```
 Client (Swagger UI / Angular)
     │  HTTP JSON, /api/v1, errors as RFC 9457 Problem Details
     ▼
 Middleware      core/middleware.py      correlation ID, request logging, CORS
     ▼
 API layer       api/routers/*           thin: validate DTOs (api/schemas.py, api/types.py),
     │                                   call a service, map the result to a DTO
     │  Depends()  api/deps.py           dependency injection (tests override these)
     ▼
 Service layer   services/*              use cases; record domain events
     ▼
 Unit of Work    infrastructure/unit_of_work.py   one transaction; publishes events to the
     │                                             EventBus → AuditHandler writes audit_logs
     ▼
 Repositories    infrastructure/repositories.py   the only place with SQL
     ▼
 Database        infrastructure/db.py             SQLAlchemy models → PostgreSQL

 Domain          domain/*   pure Python rules and events; used by every layer, imports none
 External        infrastructure/fx_client.py   Frankfurter (live) / static (tests) FX rates
```

**Where it starts:** `uvicorn spendwise.main:app` imports `main.py`, whose `create_app()`
loads settings, configures logging, creates the DB engine and event bus, adds middleware and
registers routers. The `lifespan` function opens the shared HTTP client for FX lookups and
closes resources on shutdown.

**Request flow (create a USD expense):** middleware assigns a correlation ID → the router
validates `ExpenseCreate` → awaits the USD→INR rate → runs `ExpenseService.create` in a
worker thread → the service validates the domain `Expense`, saves it through the repository
and records `ExpenseCreated` → `uow.commit()` writes the audit row and the expense in one
transaction → `201 Created` with the `X-Correlation-ID` header.

### Project layout

```
spendwise/
├── docker-compose.yml          postgres (host port 5433) + api (port 8000)
├── IMPLEMENTATION_PLAN.md      design, phases, concept map
└── backend/
    ├── Dockerfile              multi-stage, non-root, migrates on start
    ├── alembic/                migrations
    ├── scripts/seed.py         sample-data CLI
    ├── sample_data/            example CSV for import
    ├── src/spendwise/
    │   ├── main.py             app factory ← start here
    │   ├── core/               config, logging, middleware, exceptions, decorators
    │   ├── domain/             entities, rules, events
    │   ├── infrastructure/     DB, repositories, unit of work, FX client
    │   ├── services/           use cases, event bus, reports, import, export
    │   └── api/                routers, DTOs, money types, DI providers
    └── tests/                  unit/ (no DB) and integration/ (PostgreSQL)
```

## API overview

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | App version and DB connectivity (503 when the DB is down) |
| GET, POST | `/api/v1/categories` | 409 on duplicate name |
| GET, PUT, DELETE | `/api/v1/categories/{id}` | 409 when deleting a category that has expenses |
| GET, POST | `/api/v1/expenses` | `page`, `page_size` (≤100), `date_from`, `date_to`, `category_id` |
| GET, PUT, DELETE | `/api/v1/expenses/{id}` | a currency change fetches a fresh FX rate |
| POST | `/api/v1/imports/expenses` | multipart CSV: `spent_on,category,amount,currency,description` |
| GET | `/api/v1/reports/monthly` | `year`, `month` |
| GET | `/api/v1/reports/monthly/export` | `format=csv\|json`, downloads a file |
| GET | `/api/v1/fx/rates` | `symbols=USD,EUR` (1 INR = rate × symbol) |
| GET | `/api/v1/audit-logs` | `entity_type`, `entity_id`, paginated, newest first |

## Troubleshooting

- **Port 5432/8000 already in use:** SpendWise's Postgres uses host port 5433 to avoid other
  local servers; change the left side of `ports:` in `docker-compose.yml` if needed.
- **AED expenses return 502:** the Frankfurter API does not publish AED rates.
- **Integration tests fail to start:** make sure Docker Desktop is running.
