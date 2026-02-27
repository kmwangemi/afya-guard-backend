# Afya Guard — Backend API

> Kenya's AI-powered healthcare fraud detection system for the Social Health Authority (SHA).

Afya Guard protects public healthcare funds by detecting phantom patients, duplicate claims, upcoding, and fraudulent provider activity in real time — before payments are made. Built for scale, explainability, and compliance with Kenya's Data Protection Act.

---

## What It Does

| Fraud Pattern | Detection Method |
|---|---|
| **Phantom patients** | Member validity checks, coverage verification, DOB anomalies |
| **Duplicate claims** | 7-day rolling window deduplication across member + diagnosis + provider |
| **Upcoding** | SHA tariff reference comparison, inpatient-only code detection |
| **Provider anomalies** | Peer group benchmarking, statistical z-score deviation |
| **Behavioural patterns** | Member frequency abuse, bulk submissions, weekend spikes |

Every flagged claim includes a full SHAP-style explanation — so analysts understand *why* a claim was flagged, not just *that* it was.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | FastAPI + Uvicorn |
| Database | PostgreSQL (Neon serverless) |
| ORM & migrations | SQLAlchemy 2.0 + Alembic |
| ML engine | XGBoost + scikit-learn + SHAP |
| Task queue | Celery + Redis |
| Auth | JWT (python-jose) + bcrypt (passlib) |
| Package manager | uv |

---

## Prerequisites

Before you start, make sure you have:

- Python **3.11+**
- PostgreSQL running locally (or a [Neon](https://neon.tech) connection string)
- Redis running locally (for Celery background tasks)
- `uv` installed (see Step 1)

---

## Local Setup

### 1. Install uv

`uv` is a fast Python package and virtual environment manager. If you don't have it:

```bash
pip install uv
```

Verify the installation:

```bash
uv --version
```

---

### 2. Clone the repository and install dependencies

```bash
git clone https://github.com/your-org/afya-guard-backend.git
cd afya-guard-backend
uv sync
```

`uv sync` reads `pyproject.toml`, creates a `.venv` folder, and installs all dependencies in one step. No manual `pip install` needed.

---

### 3. Configure environment variables

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Then edit `.env`:

```env
# ── Application ───────────────────────────────────────────────────────────────
ENVIRONMENT=development
DEBUG=true

# ── Database (PostgreSQL / Neon) ──────────────────────────────────────────────
DATABASE_URL=postgresql://postgres:password@localhost:5432/afya_guard

# ── Authentication ────────────────────────────────────────────────────────────
SECRET_KEY=change-me-use-a-long-random-string-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

# ── Redis (Celery task queue) ─────────────────────────────────────────────────
REDIS_URL=redis://localhost:6379/0

# ── ML Model ──────────────────────────────────────────────────────────────────
ML_MODEL_PATH=ml_models/xgboost_fraud_v1.json
ML_MODEL_FALLBACK_ENABLED=true

# ── Fraud Scoring Thresholds ──────────────────────────────────────────────────
FRAUD_MEDIUM_THRESHOLD=40.0
FRAUD_HIGH_THRESHOLD=70.0
FRAUD_CRITICAL_THRESHOLD=90.0

# ── File Storage (Cloudinary — for case attachments) ──────────────────────────
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret

# ── Alerts ────────────────────────────────────────────────────────────────────
ALERT_AUTO_ESCALATE_HOURS=24
ALERT_EXPIRE_HOURS=72
```

> **Security note:** Never commit your `.env` file. It is already listed in `.gitignore`.

---

### 4. Set up the database

#### First time only — initialise Alembic

If the `alembic/` directory does not exist yet:

```bash
alembic init alembic
```

Then open `alembic/env.py` and configure it to import your models and database URL:

```python
from app.db.base import Base
from app.models import *          # ensure all models are imported
from app.core.config import settings

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata
```

#### Run all migrations

```bash
alembic upgrade head
```

> Always run this after cloning the repo, pulling new code, or creating new models. The app will not start correctly against an un-migrated database.

---

### 5. Start the development server

```bash
uv run fastapi dev app/main.py
```

The API will be available at:

| URL | Description |
|---|---|
| `http://localhost:8000` | API root (health check) |
| `http://localhost:8000/docs` | Swagger interactive documentation |
| `http://localhost:8000/redoc` | ReDoc API reference |

---

### 6. Start the Celery worker (optional — required for background scoring)

In a separate terminal:

```bash
uv run celery -A app.worker worker --loglevel=info
```

Background tasks (auto-scoring after claim ingestion, alert delivery, model retraining jobs) require Celery + Redis to be running.

---

## Project Structure

```
afya-guard-backend/
├── app/
│   ├── main.py                  # FastAPI application entry point
│   ├── core/
│   │   ├── config.py            # Environment settings (pydantic-settings)
│   │   ├── security.py          # JWT, bcrypt, token hashing
│   │   └── dependencies.py      # FastAPI Depends() — auth guards, pagination
│   ├── db/
│   │   ├── base.py              # SQLAlchemy DeclarativeBase
│   │   └── session.py           # Engine, SessionLocal, get_db()
│   ├── models/
│   │   ├── models.py            # All ORM models (16 tables)
│   │   ├── enums.py             # All PostgreSQL ENUMs (12 types)
│   │   └── fraud_alert.py       # FraudAlert + AlertNotification models
│   ├── schemas/
│   │   ├── base.py              # Shared base schemas + PaginatedResponse
│   │   ├── auth_schema.py       # Login, token, password schemas
│   │   ├── user_schema.py       # User + RBAC schemas
│   │   ├── claim_schema.py      # Claim ingestion + response schemas
│   │   ├── fraud_schema.py      # Scoring + high-risk schemas
│   │   ├── case_schema.py       # Case management schemas
│   │   └── admin_schema.py      # Rules, models, alerts, analytics schemas
│   ├── services/
│   │   ├── audit_service.py     # Immutable audit log writer
│   │   ├── auth_service.py      # Login, refresh, logout, password change
│   │   ├── user_service.py      # User CRUD + role assignment
│   │   ├── claim_service.py     # Claim ingestion + provider/member resolution
│   │   ├── feature_service.py   # Feature engineering (12 ML features)
│   │   ├── fraud_service.py     # Hybrid scoring pipeline orchestrator
│   │   ├── case_service.py      # Case lifecycle management
│   │   └── rule_model_service.py# Rule CRUD + ML model version management
│   ├── detectors/
│   │   ├── base_detector.py     # Abstract base detector interface
│   │   ├── duplicate_detector.py
│   │   ├── phantom_patient_detector.py
│   │   ├── upcoding_detector.py
│   │   └── provider_profiler.py
│   └── routes/
│       ├── auth_routes.py       # POST /auth/login|refresh|logout
│       ├── user_routes.py       # GET/POST/PATCH /users, /roles, /permissions
│       ├── claim_routes.py      # POST/GET /claims, /providers, /members
│       ├── fraud_case_routes.py # /fraud/*, /cases/*, /alerts/*
│       └── admin_routes.py      # /rules/*, /models/*, /analytics/*, /integration/*
├── alembic/                     # Database migration scripts
├── ml_models/                   # Trained model artifacts (.json / .pkl)
├── tests/                       # pytest test suite
├── .env.example                 # Environment variable template
├── pyproject.toml               # Project metadata + dependencies
└── README.md
```

---

## API Overview

All endpoints are prefixed with `/api/v1`. Full interactive docs at `/docs`.

### Authentication
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/login` | Login — returns JWT access + refresh tokens |
| `POST` | `/auth/refresh` | Exchange refresh token for new access token |
| `POST` | `/auth/logout` | Revoke refresh token (server-side logout) |
| `PATCH` | `/auth/password` | Change own password |

### Claims
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/claims` | Ingest claim + auto-score in background |
| `GET` | `/claims` | List claims with filters |
| `GET` | `/claims/{id}` | Full claim detail with features + latest score |
| `PATCH` | `/claims/{id}/status` | Update SHA claim status |
| `POST` | `/claims/{id}/score` | Manually trigger fraud scoring |
| `GET` | `/claims/{id}/features` | Get engineered ML features |
| `POST` | `/claims/{id}/features/recompute` | Re-run feature engineering |

### Fraud & Cases
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/fraud/high-risk` | List HIGH + CRITICAL risk claims |
| `GET` | `/fraud/scores/{id}` | Get score detail with SHAP explanations |
| `GET` | `/cases` | List investigation cases |
| `POST` | `/cases` | Open a case manually |
| `PATCH` | `/cases/{id}/assign` | Assign to analyst |
| `PATCH` | `/cases/{id}/status` | Update case status (OPEN → CONFIRMED / CLEARED) |
| `POST` | `/cases/{id}/notes` | Add analyst note |
| `GET` | `/alerts` | List fraud alerts |
| `PATCH` | `/alerts/{id}/acknowledge` | Acknowledge alert |
| `PATCH` | `/alerts/{id}/resolve` | Resolve or dismiss alert |

### Admin
| Method | Endpoint | Description |
|---|---|---|
| `GET/POST` | `/rules` | List / create fraud rules |
| `PATCH` | `/rules/{id}/toggle` | Activate or deactivate a rule |
| `GET/POST` | `/models` | List / register ML model versions |
| `PATCH` | `/models/{id}/deploy` | Set active scoring model |
| `GET` | `/analytics/summary` | Dashboard KPIs |
| `GET` | `/analytics/risk-distribution` | Score distribution breakdown |
| `GET` | `/analytics/provider/{id}` | Per-provider fraud profile |
| `POST` | `/integration/sha/webhook` | Receive SHA claim events |

---

## Database Migrations

### Create a migration after changing models

```bash
alembic revision --autogenerate -m "add member coverage index"
```

Review the generated file in `alembic/versions/` before applying it.

### Apply pending migrations

```bash
alembic upgrade head
```

### Roll back one migration

```bash
alembic downgrade -1
```

### Roll back to a specific revision

```bash
alembic downgrade <revision_id>
```

### Check current migration state

```bash
alembic current
```

### View full migration history

```bash
alembic history --verbose
```

For the full reference, see the [Alembic documentation](https://alembic.sqlalchemy.org/).

---

## Fraud Scoring Pipeline

When a claim is ingested, the following pipeline runs automatically:

```
Claim ingested
      │
      ▼
Feature Engineering (12 features)
      │
      ├── Rule Engine          → rule_score  (weight: 40%)
      ├── XGBoost ML Model     → ml_score    (weight: 40%)
      └── 4 Detectors (avg)    → det_score   (weight: 20%)
                │
                ├── DuplicateDetector
                ├── PhantomPatientDetector
                ├── UpcodingDetector
                └── ProviderProfiler
      │
      ▼
final_score = (rule × 0.4) + (ml × 0.4) + (detectors × 0.2)
      │
      ├── ≥ 90  → CRITICAL — auto-create FraudCase (URGENT) + alert
      ├── ≥ 70  → HIGH     — auto-create FraudCase + alert
      ├── ≥ 40  → MEDIUM   — alert only
      └──  < 40 → LOW      — logged, no action
```

Every score is stored with full SHAP-style explanations — one record per feature — so analysts see exactly which signals drove the decision.

---

## Role-Based Access Control

| Role | Key Permissions |
|---|---|
| **Fraud Analyst** | `view_claim`, `view_score`, `create_case` |
| **Senior Analyst** | + `approve_case`, `assign_case`, `update_case` |
| **Admin** | + `manage_users`, `manage_rules` |
| **Data Scientist** | + `deploy_model`, `view_features`, `manage_features` |
| **Auditor** | `view_audit_logs`, `view_analytics` |
| **System / SHA Integration** | `ingest_claim`, `score_claim` |

Permissions are enforced at the route level via `Depends(require_permission("permission_name"))`. Superusers bypass all permission checks.

---

## Running Tests

```bash
uv run pytest
```

With coverage report:

```bash
uv run pytest --cov=app --cov-report=term-missing
```

Run a specific test file:

```bash
uv run pytest tests/test_fraud_service.py -v
```

---

## Design Principles

**Non-intrusive** — Afya Guard never modifies SHA source data. It only reads claim snapshots and writes to its own database.

**No auto-blocking** — The system flags and recommends. Final decisions always rest with human analysts.

**Explainability-first** — Every fraud score stores feature-level weights. No black-box decisions.

**Audit everything** — Every state-changing action writes an immutable `AuditLog` record with the acting user, entity, and metadata.

**Modular detectors** — New fraud patterns can be added by implementing `BaseDetector` without touching the core scoring engine.

---

## Contributing

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make your changes and add tests
3. Run `uv run pytest` — all tests must pass
4. Create a migration if you changed any models: `alembic revision --autogenerate -m "description"`
5. Open a pull request against `main`

---

## License

MIT © Afya Guard / SHA Kenya