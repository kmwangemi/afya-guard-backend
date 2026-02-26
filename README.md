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

### 3. Install system dependencies (Ubuntu)

Some features require system-level packages that cannot be installed inside a virtual environment.

#### Tesseract OCR

The SHA claims extractor uses Tesseract as a fallback OCR engine for scanned/photographed PDF forms. Tesseract is a system binary — it must be installed on the machine itself, not inside your `.venv`.

```bash
sudo apt update && sudo apt install -y tesseract-ocr
```

To also support additional languages (optional):

```bash
# Swahili support (useful for Kenyan SHA forms)
sudo apt install -y tesseract-ocr-swa

# All languages
sudo apt install -y tesseract-ocr-all
```

Verify the installation:

```bash
tesseract --version
```

#### pytesseract (Python wrapper)

`pytesseract` is the Python wrapper that calls the Tesseract binary. Install it inside your `.venv`:

```bash
source .venv/bin/activate
pip install pytesseract
```

Or if you are using `uv`, it is already included in the project dependencies via `uv sync`.

Verify both are correctly wired together:

```bash
source .venv/bin/activate
python -c "import pytesseract; print(pytesseract.get_tesseract_version())"
```

You should see a version number printed without any errors.

> **Note:** `pytesseract` and `tesseract-ocr` are two separate things.
>
> - `tesseract-ocr` → the engine binary, installed system-wide via `apt`
> - `pytesseract` → the Python wrapper, installed in your `.venv` via `pip`
>
> Both are required. Installing only one will cause the error:
> `tesseract is not installed or it's not in your PATH`

#### pdf2image

`pdf2image` converts PDF pages to images before OCR is run. It depends on `poppler-utils`:

```bash
sudo apt install -y poppler-utils
```

Then install the Python package in your `.venv`:

```bash
pip install pdf2image
```

#### One-liner for all system dependencies

```bash
sudo apt update && sudo apt install -y tesseract-ocr poppler-utils
```

---

### 4. Set up environment variables

Create a `.env` file in the project root with your configuration:

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/your_database

# Auth
SECRET_KEY=your-secret-key-here
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Cloudinary (file storage)
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret

# AI extraction (at least one is required for scanned SHA claim forms)
XAI_API_KEY=your-xai-key-here           # Grok vision (primary)
ANTHROPIC_API_KEY=your-anthropic-key    # Claude vision (fallback)

# Set to "false" to force OCR+regex extraction only (no AI)
USE_AI_EXTRACTION=true
```

> **AI extraction note:** The claims extractor tries AI vision first (Grok → Claude),
> then falls back to embedded PDF text, then Tesseract OCR. For scanned or
> photographed SHA forms, having at least one AI API key set is strongly recommended
> as OCR alone struggles with handwritten fields.

---

### 5. Initialize Alembic (First time only)

If Alembic is not yet set up in the project:

```bash
alembic init alembic
```

Then configure `alembic/env.py` to import your models and database settings.

### 6. Run database migrations

Apply all pending migrations to set up your database schema:

```bash
alembic upgrade head
```

**Note:** Always run migrations before starting the application, especially after pulling new code or creating new models.

---

## 7. Running the project in your dev machine

```bash
uv run fastapi dev app/main.py
```

Access it at `http://localhost:8000`

API docs available at `http://localhost:8000/docs`

---

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

---

## Troubleshooting

### `tesseract is not installed or it's not in your PATH`

Tesseract is not installed on your system. Run:

```bash
sudo apt update && sudo apt install -y tesseract-ocr
```

Then verify:

```bash
tesseract --version
```

If it is installed but still not found, you can point pytesseract to it explicitly by adding this to `sha_claims_extractor.py`:

```python
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"/usr/bin/tesseract"
```

### `pdf2image` / `Unable to get page count` errors

Install the missing system dependency:

```bash
sudo apt install -y poppler-utils
```

### AI extraction not running

Check that at least one API key is set in your `.env`:

```bash
echo $XAI_API_KEY
echo $ANTHROPIC_API_KEY
```

If both are empty, the extractor will skip AI and fall back to OCR + regex, which may produce incomplete results for handwritten forms.
