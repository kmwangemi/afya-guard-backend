"""
SHA Fraud Detection Platform — Main Application

Assembles all routes under /api/v1 prefix.
Run with: uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes.admin_routes import router as admin_router
from app.api.v1.routes.auth_routes import router as auth_router
from app.api.v1.routes.claim_routes import router as claim_router
from app.api.v1.routes.fraud_case_routes import router as fraud_case_router
from app.api.v1.routes.user_routes import router as user_router
from app.core.config import settings

# Call load_ml_artifacts() once at startup in your lifespan hook
from app.services.fraud_service import load_ml_artifacts


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load ML model into memory once — shared across all requests
    load_ml_artifacts()
    yield
    # (shutdown logic here if needed)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Production-grade fraud intelligence platform for the Social Health Authority (SHA) Kenya. "
        "Hybrid rule + ML scoring engine with full explainability, case management, and audit trails."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
PREFIX = "/api/v1"

app.include_router(auth_router, prefix=PREFIX)
app.include_router(user_router, prefix=PREFIX)
app.include_router(claim_router, prefix=PREFIX)
app.include_router(fraud_case_router, prefix=PREFIX)
app.include_router(admin_router, prefix=PREFIX)


@app.get("/", tags=["Health"])
def root():
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok"}
