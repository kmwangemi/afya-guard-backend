# afya-guard-backend

Backend API for Afya Guard - Kenya's AI-powered healthcare fraud detection system for Social Health Authority (SHA). Detects phantom patients, upcoding, duplicate claims, and fraudulent providers using machine learning, saving billions in public healthcare funds. Built with FastAPI, PostgreSQL, Redis, scikit-learn, Celery and XGBoost.

## Install required packages

### 1. Install uv (if not already installed)

```bash
pip install uv
```

### 2. Install dependencies

`uv` automatically manages virtual environments and dependencies.

```bash
uv sync
```

This will create a virtual environment and install all packages from your `pyproject.toml` or `requirements.txt`.

Or if you are using `uv`, it is already included in the project dependencies via `uv sync`.

Verify both are correctly wired together:

---

### 3. Set up environment variables

Create a `.env` file in the project root with your configuration:

# Database

DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/your_database

# Auth

SECRET_KEY=your-secret-key-here
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Cloudinary (file storage)

CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret

### 4. Initialize Alembic (First time only)

If Alembic is not yet set up in the project:

```bash
alembic init alembic
```

Then configure `alembic/env.py` to import your models and database settings.

### 5. Run database migrations

Apply all pending migrations to set up your database schema:

```bash
alembic upgrade head
```

**Note:** Always run migrations before starting the application, especially after pulling new code or creating new models.

---

## 6. Running the project in your dev machine

```bash
uv run fastapi dev app/main.py
```

Access it at `http://localhost:8000`

API docs available at `http://localhost:8000/docs`

## Working with Database Migrations

### Creating a new migration after model changes

```bash
alembic revision --autogenerate -m "description of changes"
```

### Applying migrations

```bash
alembic upgrade head
```

### Rolling back a migration

```bash
alembic downgrade -1
```

### Check current migration status

```bash
alembic current
```

For more Alembic commands, see the [Alembic documentation](https://alembic.sqlalchemy.org/).
