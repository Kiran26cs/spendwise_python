# SpendWise — Implementation Plan

> A small, production-style **personal expense tracker** built to learn Python end to end — from basics to advanced concepts — using industry-standard tools and design patterns.
> Backend: **Python + FastAPI**. Frontend: **Angular** (final phase).

---

## 0. Development guidelines

1. **One phase at a time.** Each phase is a reviewable milestone: it is complete only when all quality gates (Section 10) pass.
2. **No dependencies** beyond those listed in Section 3 unless agreed first.
3. **Follow the folder structure** in Section 5 exactly. Do not invent new top-level layers.
4. **Every module starts with a docstring** that states its responsibility and lists the Python concepts it demonstrates, e.g.:
   ```python
   """Unit of Work over a SQLAlchemy session.

   Concepts: context managers (__enter__/__exit__), Protocol, type hints.
   """
   ```
5. **Type-hint everything.** The code must pass `mypy --strict`.
6. **Write tests alongside code.** Each phase lists the tests it requires.
7. **Keep it simple.** This is a learning project: prefer clear, explicit code over clever code. No premature abstractions beyond the patterns listed here.
8. **Money is exact end to end.** The API contract uses JSON numbers; inside Python money is always `Decimal`; the database stores `NUMERIC`. `float` is never used for money arithmetic or storage. See Section 8.6.

---

## 1. Goals

- Exercise Python's **basic → intermediate → advanced** features in real code (see the concept map in Section 11).
- Apply **widely used industry design patterns** for data access, API contracts, logging and auditing.
- Stay **small** (~25 backend source files) and easy to understand.

### Non-goals (for now)

- Authentication and authorization (no users; single-user app).
- Async database access (the DB layer is sync; async is used for external HTTP calls).
- Background job queues and caching servers (Redis, Celery).

---

## 2. Features

| # | Feature | Notes |
|---|---|---|
| F1 | Manage categories | CRUD; each category has an optional monthly budget |
| F2 | Manage expenses | CRUD, filter by date range and category, paginated list |
| F3 | Multi-currency expenses | Foreign-currency amounts are converted to the base currency (INR) using a live FX API |
| F4 | Monthly report | Totals per category, budget used %, over-budget flag |
| F5 | CSV import | Upload a CSV of expenses; streamed and validated row by row; returns a summary of imported/failed rows |
| F6 | Export | Download the monthly report as CSV or JSON |
| F7 | Audit trail | Every create/update/delete is recorded in an audit log, viewable through the API |
| F8 | Observability | Structured JSON logs with a correlation ID for every request |
| F9 | Angular UI | Dashboard, expenses, categories, import, audit log |

---

## 3. Tech stack

### Backend

| Concern | Tool |
|---|---|
| Language | Python **3.12+** |
| Package / env manager | **uv** (`pyproject.toml`, `uv.lock`) |
| Web framework | **FastAPI** + **Uvicorn** |
| Validation / DTOs | **Pydantic v2** |
| Configuration | **pydantic-settings** (`.env` file) |
| ORM | **SQLAlchemy 2.0** (typed `Mapped[]` declarative style, sync) |
| Migrations | **Alembic** |
| Database | **PostgreSQL** in every environment (local via Docker), driver `psycopg[binary]`. SQLite is not used because it has no exact decimal type. |
| HTTP client | **httpx** (async) |
| File upload | `python-multipart` |
| Tests | **pytest**, `pytest-cov`, `respx` (mock httpx), **testcontainers** (throwaway PostgreSQL for integration tests) |
| Lint + format | **ruff** |
| Type checking | **mypy** (strict) |
| Git hooks | **pre-commit** (ruff, mypy) |
| Container | Docker + docker-compose |

### Frontend (Phase 7)

| Concern | Tool |
|---|---|
| Framework | **Angular** (latest stable; standalone components, signals, new control flow `@if/@for`) |
| UI components | **Angular Material** |
| HTTP | `HttpClient` + functional interceptors |
| API client | **ng-openapi-gen** — generated from the FastAPI OpenAPI spec |
| Dev API access | `proxy.conf.json` (no CORS needed locally) |

### External service

- FX rates: **Frankfurter API** (`https://api.frankfurter.dev/v1`) — free, no API key.

---

## 4. Architecture

### 4.1 Layers

```
 Angular UI
     │  HTTP / JSON  (REST, /api/v1, Problem Details errors)
     ▼
 API layer        api/routers/*       thin: parse DTOs, call service, map result → DTO
     │  Depends() (Dependency Injection)
     ▼
 Service layer    services/*          business use cases, raises domain events
     │
     ▼
 Unit of Work     infrastructure/unit_of_work.py   transaction boundary, owns repositories
     │
     ▼
 Repositories     infrastructure/repositories.py   collection-like access to aggregates
     │
     ▼
 ORM / DB         infrastructure/db.py             SQLAlchemy models, engine, session

 Domain           domain/*   pure Python (dataclasses, enums, rules, events) — imported by all, imports nothing from other layers
```

**Dependency rule:** outer layers depend on inner layers, never the reverse. `domain/` has **no** imports from FastAPI, SQLAlchemy or Pydantic.

### 4.2 Design patterns (and where they live)

| Concern | Pattern | Location | Key idea |
|---|---|---|---|
| DB access | **Repository** | `infrastructure/repositories.py` | Services talk to `repo.get()/add()/list()`; queries are hidden. Generic base `SqlAlchemyRepository[T]` + `Protocol` interface. |
| Transactions | **Unit of Work** | `infrastructure/unit_of_work.py` | `with uow:` → commit on `uow.commit()`, automatic rollback on exception or exit without commit. Holds repositories and collected domain events. |
| Business logic | **Service Layer** | `services/*.py` | Use-case functions/classes; no HTTP or SQL details. |
| Wiring | **Dependency Injection** | `api/deps.py` | FastAPI `Depends()` provides UoW, services, settings. Tests override dependencies. |
| API ↔ UI contract | **DTO + REST + OpenAPI** | `api/schemas.py` | Pydantic request/response models separate from domain and ORM models. `/api/v1` versioning. |
| API errors | **Problem Details (RFC 9457)** | `main.py` exception handlers | Every error is `application/problem+json`. |
| Pagination | **Generic Page DTO** | `api/schemas.py` | `Page[T]` with `items, total, page, page_size`. |
| Auditing | **Observer / Domain Events** | `domain/events.py`, `services/event_bus.py` | Services record events on the UoW; on commit, the bus dispatches them to handlers. `AuditHandler` writes an `audit_log` row **in the same transaction** (atomic). |
| Logging | **Structured logging + Correlation ID** | `core/logging.py`, `core/middleware.py` | JSON logs; middleware sets a correlation ID in a `ContextVar`; a logging `Filter` injects it into every record. |
| Cross-cutting behaviour | **Decorator** | `core/decorators.py` | `@log_execution_time`, `@retry(...)`. |
| Export formats | **Strategy** | `services/exporters.py` | `CsvExporter`, `JsonExporter` implement one `Exporter` protocol. |
| FX providers | **Strategy** | `infrastructure/fx_client.py` | `FrankfurterFxProvider` (real) and `StaticFxProvider` (tests/offline). |
| Object creation | **Factory** | `main.py` (`create_app()`), `services/exporters.py` (`get_exporter(fmt)`) | Central creation; easy testing. |
| Config | **Settings object (12-factor)** | `core/config.py` | All config from env vars / `.env`; cached with `@lru_cache`. |
| Errors | **Exception hierarchy** | `core/exceptions.py` | Domain exceptions mapped to HTTP status in one place. |

### 4.3 Request flow example — "Create expense in USD"

1. `POST /api/v1/expenses` → middleware assigns correlation ID → router validates `ExpenseCreate` DTO.
2. Router (`async def`) awaits `fx_provider.get_rate("USD", "INR")` (async httpx, with `@retry`).
3. Router calls the sync service via `run_in_threadpool(expense_service.create, ...)`.
4. Service opens `with uow:` → builds domain `Expense` (validation in `__post_init__`) → `uow.expenses.add(expense)` → `uow.record(ExpenseCreated(...))` → `uow.commit()`.
5. `commit()` dispatches events → `AuditHandler` adds an `audit_log` row → single DB commit.
6. Router maps domain → `ExpenseRead` DTO → `201 Created`. Every log line in the flow carries the same correlation ID.

---

## 5. Folder structure

```
spendwise/
├── README.md
├── IMPLEMENTATION_PLAN.md
├── docker-compose.yml
├── .gitignore
├── .pre-commit-config.yaml
├── backend/
│   ├── pyproject.toml
│   ├── .env.example
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   ├── scripts/
│   │   └── seed.py                  # CLI (argparse) to seed sample data
│   ├── sample_data/
│   │   └── expenses_sample.csv
│   ├── src/spendwise/
│   │   ├── __init__.py
│   │   ├── main.py                  # create_app(), middleware, exception handlers, router registration
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py            # Settings, get_settings()
│   │   │   ├── logging.py           # JSON formatter, correlation filter, configure_logging()
│   │   │   ├── middleware.py        # CorrelationIdMiddleware, request logging
│   │   │   ├── context.py           # correlation_id ContextVar
│   │   │   ├── decorators.py        # log_execution_time, retry
│   │   │   └── exceptions.py        # exception hierarchy
│   │   ├── domain/
│   │   │   ├── __init__.py
│   │   │   ├── models.py            # Category, Expense (dataclasses), enums
│   │   │   └── events.py            # DomainEvent base + concrete events
│   │   ├── infrastructure/
│   │   │   ├── __init__.py
│   │   │   ├── db.py                # engine, SessionLocal, ORM models (Base)
│   │   │   ├── mappers.py           # ORM <-> domain mapping functions
│   │   │   ├── repositories.py      # Repository protocols + SQLAlchemy implementations
│   │   │   ├── unit_of_work.py      # AbstractUnitOfWork, SqlAlchemyUnitOfWork
│   │   │   └── fx_client.py         # FxProvider protocol, Frankfurter + Static providers, TTL cache
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── event_bus.py         # EventBus (Observer), AuditHandler
│   │   │   ├── category_service.py
│   │   │   ├── expense_service.py
│   │   │   ├── report_service.py    # monthly report (generators, itertools, functools)
│   │   │   ├── importer.py          # streaming CSV import
│   │   │   └── exporters.py         # Exporter strategy + get_exporter factory
│   │   └── api/
│   │       ├── __init__.py
│   │       ├── deps.py              # DI providers
│   │       ├── types.py             # Money, FxRate annotated types (exact numeric contract)
│   │       ├── schemas.py           # Pydantic DTOs, Page[T], ProblemDetail
│   │       └── routers/
│   │           ├── __init__.py
│   │           ├── health.py
│   │           ├── categories.py
│   │           ├── expenses.py
│   │           ├── reports.py
│   │           ├── imports.py
│   │           ├── fx.py
│   │           └── audit.py
│   └── tests/
│       ├── conftest.py              # fixtures: settings, PostgreSQL (testcontainers), client, fake UoW
│       ├── fakes.py                 # FakeRepository, FakeUnitOfWork
│       ├── unit/
│       └── integration/
└── frontend/                        # Phase 7 — Angular app
```

---

## 6. Data model

### 6.1 Domain (pure Python, `domain/models.py`)

```python
class Currency(StrEnum): INR, USD, EUR, GBP, AED, SGD
class ExportFormat(StrEnum): CSV = "csv", JSON = "json"

@dataclass
class Category:
    id: int | None
    name: str
    monthly_budget: Decimal | None = None
    # rule: name non-empty, trimmed, ≤ 50 chars; budget ≥ 0
    def budget_status(self, spent: Decimal) -> "BudgetStatus": ...

@dataclass
class Expense:
    id: int | None
    category_id: int
    amount: Decimal              # original amount, > 0, 2 decimal places
    currency: Currency
    amount_base: Decimal         # converted to base currency
    fx_rate: Decimal             # rate used (1 for base currency)
    description: str
    spent_on: date               # rule: not in the future
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # __post_init__ validates rules; raises DomainValidationError
    @property
    def is_foreign(self) -> bool: ...

@dataclass(frozen=True)
class BudgetStatus:
    budget: Decimal | None
    spent: Decimal
    remaining: Decimal | None
    used_percent: Decimal | None
    over_budget: bool
```

### 6.2 Tables (`infrastructure/db.py`)

| Table | Columns |
|---|---|
| `categories` | `id` PK, `name` UNIQUE NOT NULL, `monthly_budget` NUMERIC(12,2) NULL, `created_at`, `updated_at` |
| `expenses` | `id` PK, `category_id` FK → categories (RESTRICT on delete), `amount` NUMERIC(12,2), `currency` VARCHAR(3), `amount_base` NUMERIC(12,2), `fx_rate` NUMERIC(18,6), `description` VARCHAR(200), `spent_on` DATE (indexed), `created_at`, `updated_at` |
| `audit_logs` | `id` PK, `event_type` VARCHAR(50), `entity_type` VARCHAR(50), `entity_id` INT, `payload` JSON, `correlation_id` VARCHAR(64), `occurred_at` TIMESTAMP (indexed) |

- Money columns use SQLAlchemy `Numeric(12, 2, asdecimal=True)`; FX rate uses `Numeric(18, 6, asdecimal=True)`. PostgreSQL `NUMERIC` stores these values exactly.
- Timestamps are UTC, timezone-aware.
- Deleting a category with expenses → `ConflictError` (409).

### 6.3 Domain events (`domain/events.py`)

Frozen dataclasses inheriting `DomainEvent` (`occurred_at` default factory, `event_type` property returning class name):

`CategoryCreated`, `CategoryUpdated`, `CategoryDeleted`, `ExpenseCreated`, `ExpenseUpdated`, `ExpenseDeleted`, `ExpensesImported` (count).

---

## 7. API contract (`/api/v1`)

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| GET | `/health` | — | `{status, version, database}` | Not versioned; checks DB connectivity |
| GET | `/api/v1/categories` | — | `list[CategoryRead]` | |
| POST | `/api/v1/categories` | `CategoryCreate` | `201 CategoryRead` | 409 on duplicate name |
| GET | `/api/v1/categories/{id}` | — | `CategoryRead` | 404 |
| PUT | `/api/v1/categories/{id}` | `CategoryUpdate` | `CategoryRead` | |
| DELETE | `/api/v1/categories/{id}` | — | `204` | 409 if it has expenses |
| GET | `/api/v1/expenses` | query: `page`, `page_size` (≤100), `date_from`, `date_to`, `category_id` | `Page[ExpenseRead]` | sorted by `spent_on` desc |
| POST | `/api/v1/expenses` | `ExpenseCreate` | `201 ExpenseRead` | FX conversion if currency ≠ base |
| GET | `/api/v1/expenses/{id}` | — | `ExpenseRead` | |
| PUT | `/api/v1/expenses/{id}` | `ExpenseUpdate` | `ExpenseRead` | re-converts if amount/currency changed |
| DELETE | `/api/v1/expenses/{id}` | — | `204` | |
| POST | `/api/v1/imports/expenses` | multipart CSV file | `ImportResult {imported, failed, errors: [{row, message}]}` | max 5 MB |
| GET | `/api/v1/reports/monthly` | `year`, `month` | `MonthlyReport` | per-category totals + budget status + grand total |
| GET | `/api/v1/reports/monthly/export` | `year`, `month`, `format=csv\|json` | file download | `Content-Disposition: attachment` |
| GET | `/api/v1/fx/rates` | `symbols=USD,EUR` | `{base, rates: {USD: ..}}` | fetched concurrently with `asyncio.gather` |
| GET | `/api/v1/audit-logs` | `page`, `page_size`, `entity_type`, `entity_id` | `Page[AuditLogRead]` | newest first |

### Conventions

- JSON field names: `snake_case`.
- Money in JSON: **number** with at most 2 decimal places (e.g. `1250.50`). FX rates: number with at most 6 decimal places. Strings are rejected. Full rules in Section 8.6.
- Dates: ISO 8601 (`2026-09-27`), datetimes in UTC with `Z`.
- Every response includes header `X-Correlation-ID` (echoed from request or generated as UUID4).

### Error format (RFC 9457 Problem Details)

```json
{
  "type": "about:blank",
  "title": "Not Found",
  "status": 404,
  "detail": "Expense 42 not found",
  "instance": "/api/v1/expenses/42",
  "correlation_id": "3f7c…"
}
```

| Exception | HTTP |
|---|---|
| `NotFoundError` | 404 |
| `DomainValidationError`, Pydantic `RequestValidationError` | 422 (include `errors` list) |
| `ConflictError` | 409 |
| `ExternalServiceError` | 502 |
| Any unhandled `Exception` | 500 — generic message, **no stack trace** in response; full trace logged |

### CSV import format

```csv
spent_on,category,amount,currency,description
2026-09-01,Groceries,1250.50,INR,Weekly vegetables
2026-09-03,Travel,45.00,USD,Airport taxi
```
Unknown category → row error (not auto-created). Valid rows are imported even if others fail.

---

## 8. Cross-cutting specifications

### 8.1 Configuration (`core/config.py`)

`Settings(BaseSettings)` fields: `app_name`, `app_version`, `environment` (`local|test|prod`), `debug`, `database_url` (default `postgresql+psycopg://spendwise:spendwise@localhost:5433/spendwise`), `log_level`, `log_json` (bool), `base_currency` (default `INR`), `fx_api_url`, `fx_timeout_seconds`, `fx_cache_ttl_seconds`, `cors_origins` (list), `max_upload_mb`.
`get_settings()` decorated with `@lru_cache`. Provide `.env.example`.

### 8.2 Logging (`core/logging.py`, `core/middleware.py`, `core/context.py`)

- Configure with `logging.config.dictConfig` in `configure_logging(settings)`.
- Custom `JsonFormatter(logging.Formatter)` → one JSON object per line: `timestamp, level, logger, message, correlation_id, module, function, line`, plus `exc_info` when present and any `extra` fields.
- Human-readable formatter when `log_json=False` (local dev).
- `CorrelationIdFilter(logging.Filter)` reads `correlation_id_var: ContextVar[str]`.
- `CorrelationIdMiddleware`: read `X-Correlation-ID` header or generate UUID4, set the ContextVar, log `request.start` / `request.end` with method, path, status, `duration_ms`, and add the header to the response.
- Use `logger = logging.getLogger(__name__)` per module. Never use `print`.
- Never log secrets or full request bodies.

### 8.3 Auditing (`services/event_bus.py`)

- `EventBus` holds `dict[type[DomainEvent], list[Handler]]`; `subscribe(event_type, handler)` and `publish(event, uow)`.
- `AbstractUnitOfWork` exposes `record(event)`; `commit()` publishes all collected events **before** the DB commit so audit rows are part of the same transaction, then clears the event list.
- `AuditHandler` converts any event to an `audit_logs` row (`dataclasses.asdict` for payload, current correlation ID).
- Handlers registered at startup in `create_app()`.

### 8.4 Decorators (`core/decorators.py`)

- `@log_execution_time` — works on **both** sync and async functions (detect with `inspect.iscoroutinefunction`); uses `functools.wraps`; logs at DEBUG.
- `@retry(times=3, delay_seconds=0.5, exceptions=(httpx.HTTPError,))` — decorator factory (closure), async-aware, exponential backoff, logs each retry at WARNING.

### 8.5 FX client (`infrastructure/fx_client.py`)

- `FxProvider(Protocol)`: `async def get_rate(self, from_: Currency, to: Currency) -> Decimal` and `async def get_rates(self, base, symbols) -> dict[Currency, Decimal]`.
- `FrankfurterFxProvider` uses a shared `httpx.AsyncClient` (created in app lifespan, closed on shutdown), `@retry`, timeout from settings, simple in-memory TTL cache class (`_TtlCache`) keyed by currency pair.
- `get_rates` fetches symbols concurrently with `asyncio.gather`.
- `StaticFxProvider` returns fixed rates — used in tests and when `environment=test`.
- Failures raise `ExternalServiceError`.

### 8.6 Money and numeric precision

Money is exact at every layer:

| Layer | Type | Example |
|---|---|---|
| Angular model (generated) | `number` | `1250.5` |
| JSON on the wire | number | `1250.50` |
| OpenAPI schema | `type: number`, `multipleOf: 0.01`, `minimum: 0.01`, `maximum: 9999999999.99` | |
| Pydantic DTO field | `Money` (annotated `Decimal`) | `Decimal("1250.50")` |
| Domain / services | `Decimal` | `Decimal("1250.50")` |
| PostgreSQL | `NUMERIC(12,2)` | `1250.50` |

**Can the API receive decimal numbers?** Yes. JSON has one numeric type ("number"); there is no separate float or decimal on the wire. The request body is parsed and the value is then converted to `Decimal` using the number's shortest text form, so `1250.50` becomes exactly `Decimal("1250.5")`, never `1250.4999…`. This is exact for any value up to 15 significant digits, and our money range (12 digits, 2 decimals) is well inside that. Validation then rejects anything with more than 2 decimal places instead of silently rounding.

**Implementation (`api/types.py`):**

```python
def _reject_non_numbers(value: object) -> object:
    # bool is a subclass of int, and strings would bypass the numeric contract
    if isinstance(value, (bool, str)):
        raise ValueError("must be a JSON number")
    return value

Money = Annotated[
    Decimal,
    BeforeValidator(_reject_non_numbers),
    Field(gt=0, max_digits=12, decimal_places=2),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "multipleOf": 0.01,
                    "minimum": 0.01, "maximum": 9999999999.99}),
]

Budget = Annotated[   # like Money, but 0 is allowed (a budget may be zero)
    Decimal,
    BeforeValidator(_reject_non_numbers),
    Field(ge=0, max_digits=12, decimal_places=2),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "multipleOf": 0.01,
                    "minimum": 0, "maximum": 9999999999.99}),
]

FxRate = Annotated[
    Decimal,
    BeforeValidator(_reject_non_numbers),
    Field(gt=0, max_digits=18, decimal_places=6),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "multipleOf": 0.000001, "exclusiveMinimum": 0}),
]
```

- Use `Money` / `FxRate` for **every** monetary field in request and response DTOs. Budgets use `Budget | None` (null = no budget; 0 allowed). With a budget of 0, `used_percent` is `None` and `over_budget` is `spent > 0`.
- `PlainSerializer(float, ...)` only formats the final JSON output. Values within our range convert exactly, and no calculation ever happens on the float.
- **Rounding:** converted amounts are computed as `(amount * fx_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`. All rounding goes through a single helper, `round_money()`, in `domain/models.py`.
- **Aggregation:** totals and budget percentages are computed in Python with `Decimal` (or in SQL with `SUM` over `NUMERIC`). The UI never sums money itself; it displays totals returned by the API.
- **CSV import:** amounts are parsed from text directly with `Decimal(text)` (no float step) and validated with the same rules.
- **Invalid input:** `12.345`, `"12.50"`, `true`, `0` and negative numbers all return `422` Problem Details with a field-level error.

---

## 9. Implementation phases

Each phase ends with all quality gates green (Section 10) and a short summary of what was built, so it can be reviewed before the next phase starts.

### Phase 1 — Foundation

- [x] Create repo layout, `backend/pyproject.toml` (uv), dependencies, ruff + mypy + pytest config in `pyproject.toml`, `.pre-commit-config.yaml`, `.gitignore`, `.env.example`, root `README.md`.
- [x] Root `docker-compose.yml` with a `postgres` service (named volume, healthcheck) for local development.
- [x] `core/config.py`, `core/context.py`, `core/logging.py`, `core/middleware.py`, `core/exceptions.py`, `core/decorators.py`.
- [x] `main.py` with `create_app()` factory, lifespan handler, CORS, correlation middleware, Problem Details exception handlers.
- [x] `api/routers/health.py` (DB check stubbed until Phase 3).
- **Tests:** settings load from env; JSON formatter output; correlation ID header generated/echoed; `@retry` retries then raises; `@log_execution_time` on sync and async; 404/500 return Problem Details.
- **Done when:** `uv run uvicorn spendwise.main:app --reload` serves `/health` and `/docs`.

### Phase 2 — Domain

- [x] `domain/models.py`: enums, `Category`, `Expense`, `BudgetStatus`, `round_money()`, validation in `__post_init__`, `@property`, `__str__`/`__repr__` where useful.
- [x] `domain/events.py`: `DomainEvent` base + concrete events.
- **Tests (unit, parametrized):** valid/invalid amounts, future dates, name rules, budget status math (under, exact, over, no budget).

### Phase 3 — Persistence

- [x] `infrastructure/db.py`: engine, `SessionLocal`, ORM models with `Mapped[]`.
- [x] Alembic setup + initial migration.
- [x] `infrastructure/mappers.py`: ORM ↔ domain functions.
- [x] `infrastructure/repositories.py`: `CategoryRepository`, `ExpenseRepository`, `AuditLogRepository` protocols; generic `SqlAlchemyRepository[T]` base; filtering + pagination in `ExpenseRepository.list(...)` returning `(items, total)`.
- [x] `infrastructure/unit_of_work.py`: `AbstractUnitOfWork` (context manager, `record()`, `commit()`, `rollback()`), `SqlAlchemyUnitOfWork`.
- [x] `tests/fakes.py`: `FakeRepository`, `FakeUnitOfWork`.
- [x] Health endpoint performs a real `SELECT 1`.
- **Tests (integration, PostgreSQL via testcontainers, one container per test session, each test in a rolled-back transaction):** add/get/list/filter/paginate; UoW rolls back when `commit()` not called or on exception; `Decimal("1250.50")` saved and read back is exactly equal.

### Phase 4 — Services + API (Categories & Expenses)

- [x] `services/event_bus.py` with `EventBus` + `AuditHandler`.
- [x] `services/category_service.py`, `services/expense_service.py` (record events on every change).
- [x] `infrastructure/fx_client.py` (both providers).
- [x] `api/types.py`: `Money`, `Budget`, `FxRate` (Section 8.6).
- [x] `api/schemas.py`: DTOs using `Money`/`FxRate` for all monetary fields, `Page[T]` generic, `ProblemDetail`.
- [x] `api/deps.py`: `get_settings`, `get_uow`, `get_fx_provider`, service providers.
- [x] Routers: `categories.py`, `expenses.py`, `audit.py`, `fx.py` (rates endpoint).
- **Tests:** service unit tests with `FakeUnitOfWork` + `StaticFxProvider`; API integration tests with `TestClient` and dependency overrides; FX provider tested with `respx`; verify an audit row is written for each create/update/delete.
- **Money tests (parametrized):** `1250.50`, `0.01`, `9999999999.99` accepted and returned unchanged; `12.345`, `"12.50"`, `true`, `0`, `-5` rejected with 422; `0.1 + 0.2`-style values stored exactly; USD→INR conversion rounds half-up to 2 places; OpenAPI schema for amount fields is `type: number`.

### Phase 5 — Reports, Import, Export

- [x] `services/report_service.py`: monthly report using generator pipelines, `itertools.groupby`, `collections.defaultdict`, `functools.reduce`, `Decimal` rounding.
- [x] `services/importer.py`: stream uploaded file with `csv.DictReader` via a generator; validate row by row; insert in batches with `itertools.batched(rows, 100)`; collect errors; publish `ExpensesImported`.
- [x] `services/exporters.py`: `Exporter` protocol, `CsvExporter`, `JsonExporter`, `get_exporter(fmt)` factory (dict registry).
- [x] Routers: `reports.py`, `imports.py`.
- [x] `sample_data/expenses_sample.csv`, `scripts/seed.py` (argparse: `--categories`, `--expenses N`, `--reset`; `if __name__ == "__main__":`).
- **Tests:** report totals and budget flags; import with mixed valid/invalid rows; exporters produce correct output; file-size limit enforced.

### Phase 6 — Hardening & Delivery

- [x] `backend/Dockerfile` (multi-stage, non-root user, `uv sync --frozen`).
- [x] Add the `api` service to `docker-compose.yml` (`frontend` added in Phase 7); run Alembic migrations on start.
- [x] Coverage ≥ **80%**; `mypy --strict` and `ruff` clean.
- [x] README: setup, run, test, env vars, architecture diagram, concept map link.

### Phase 7 — Angular UI

- [ ] Scaffold `frontend/` with Angular CLI (standalone, routing, SCSS) + Angular Material.
- [ ] Generate API client with **ng-openapi-gen** from `http://localhost:8000/openapi.json` into `src/app/api/` (npm script `generate:api`). Generated code is never edited by hand.
- [ ] `proxy.conf.json` → `/api` to `http://localhost:8000`.
- [ ] Functional HTTP interceptors: **correlation-id** (adds `X-Correlation-ID`), **error** (reads Problem Details → Material snackbar with `detail` + correlation ID).
- [ ] Feature folders (lazy-loaded routes), state with **signals**:
  - `dashboard/` — month picker, category totals, budget progress bars, export buttons.
  - `expenses/` — paginated Material table with filters; create/edit dialog with reactive forms and validation mirroring the API rules.
  - `categories/` — list + create/edit/delete (show 409 message when category is in use).
  - `import/` — CSV upload, show `ImportResult` with row errors.
  - `audit/` — paginated audit log table.
- [ ] Money fields are `number` in the generated models. Forms validate `> 0` and at most 2 decimal places (matching the API) and send the value as a JSON number. Money is displayed with the Angular `currency` pipe. The UI never adds up or converts money itself; totals come from the API.
- [ ] Add `frontend` service (nginx serving the build, proxying `/api`) to `docker-compose.yml`.
- **Tests:** component tests for the expense form validation and the error interceptor.

---

## 10. Quality gates (run before finishing every phase)

```bash
cd backend
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --cov=spendwise --cov-report=term-missing
```

Frontend (Phase 7):

```bash
cd frontend
npm run lint
npm test
npm run build
```

### Coding standards

- PEP 8 via ruff; max line length 100.
- Google-style docstrings for public classes and functions.
- Naming: `snake_case` functions/variables, `PascalCase` classes, `UPPER_CASE` constants.
- No business logic in routers; no SQL outside repositories; no HTTP concerns in services.
- Explicit `__all__` not required; keep imports absolute (`from spendwise.core.config import ...`).
- Commit messages: Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`).

---

## 11. Python concept map (learning guide)

| Level | Concept | Where to find it |
|---|---|---|
| Basic | Variables, types, operators, f-strings | `domain/models.py`, `services/importer.py` |
| Basic | `if/elif/else`, `for`, `while`, `match` statement | `services/importer.py` (row parsing), `services/exporters.py` (`match fmt:`) |
| Basic | Lists, dicts, sets, tuples | `services/report_service.py` |
| Basic | List/dict/set comprehensions, generator expressions | `services/report_service.py`, `api/routers/*` (DTO mapping) |
| Basic | Functions, default args, `*args/**kwargs`, keyword-only args | `core/decorators.py`, `infrastructure/repositories.py` |
| Basic | Modules, packages, absolute imports, `__init__.py` | whole `src/spendwise` layout |
| Basic | `if __name__ == "__main__":`, `argparse` | `scripts/seed.py` |
| Basic | Exceptions: `try/except/else/finally`, custom hierarchy, `raise ... from` | `core/exceptions.py`, `services/importer.py`, `infrastructure/fx_client.py` |
| Basic | File I/O, `pathlib`, `csv`, `json` | `services/importer.py`, `services/exporters.py`, `scripts/seed.py` |
| Intermediate | Classes, `__init__`, instance vs class attributes | `services/event_bus.py`, `infrastructure/fx_client.py` |
| Intermediate | Inheritance, `super()`, abstract base classes (`abc`) | `infrastructure/unit_of_work.py`, `core/exceptions.py` |
| Intermediate | `@property`, `@staticmethod`, `@classmethod` | `domain/models.py`, `domain/events.py` |
| Intermediate | Dunder methods (`__repr__`, `__eq__`, `__enter__`, `__exit__`) | `domain/models.py`, `infrastructure/unit_of_work.py` |
| Intermediate | `dataclasses` (`frozen`, `field(default_factory)`, `__post_init__`, `asdict`) | `domain/models.py`, `domain/events.py`, `services/event_bus.py` |
| Intermediate | `Enum` / `StrEnum` | `domain/models.py` |
| Intermediate | `Decimal`, `datetime`, `zoneinfo`/UTC | `domain/models.py`, `services/report_service.py` |
| Intermediate | `collections` (`defaultdict`, `Counter`) | `services/report_service.py`, `services/importer.py` |
| Intermediate | Logging module (handlers, formatters, filters, `dictConfig`) | `core/logging.py` |
| Advanced | Type hints, `Optional`/`\|`, `TypeVar`, `Generic`, `Protocol` | `infrastructure/repositories.py`, `api/schemas.py` (`Page[T]`), `services/exporters.py` |
| Advanced | `typing.Annotated` with validators and serializers | `api/types.py` |
| Advanced | Decorators with `functools.wraps` | `core/decorators.py` |
| Advanced | Closures, decorator factories | `core/decorators.py` (`retry(...)`) |
| Advanced | Context managers (class-based and `@contextmanager`) | `infrastructure/unit_of_work.py`, `infrastructure/db.py` |
| Advanced | Iterators & generators (`yield`, `yield from`, lazy pipelines) | `services/importer.py`, `services/report_service.py` |
| Advanced | `itertools` (`groupby`, `batched`, `chain`) | `services/report_service.py`, `services/importer.py` |
| Advanced | `functools` (`lru_cache`, `partial`, `reduce`) | `core/config.py`, `services/report_service.py`, `services/exporters.py` |
| Advanced | `async`/`await`, `asyncio.gather`, async context managers | `infrastructure/fx_client.py`, `main.py` (lifespan) |
| Advanced | Sync ↔ async bridging (`run_in_threadpool`) | `api/routers/expenses.py` |
| Advanced | `contextvars` | `core/context.py`, `core/middleware.py` |
| Advanced | Callables as first-class objects, registries | `services/event_bus.py`, `services/exporters.py` |
| Advanced | Introspection (`inspect.iscoroutinefunction`) | `core/decorators.py` |
| Testing | pytest fixtures, `parametrize`, fakes vs mocks, dependency overrides | `tests/` |
| Tooling | Virtual envs, `pyproject.toml`, lockfiles, linters, type checker | `backend/pyproject.toml`, `.pre-commit-config.yaml` |

---

## 12. Definition of done (whole project)

- All features F1–F9 work through the Angular UI and through `/docs`.
- `docker compose up` starts Postgres, API (with migrations applied) and the UI.
- All quality gates pass; backend coverage ≥ 80%.
- Every create/update/delete appears in the audit log with a correlation ID matching the API logs.
- README explains setup, architecture and the concept map.
