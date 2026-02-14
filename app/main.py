from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers.auth_router import auth_router
from app.api.v1.routers.claim_router import claim_router
from app.services.scheduler import start_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage application lifecycle - startup and shutdown events"""
    # Startup: Start the scheduler
    start_scheduler()
    yield
    # Shutdown: Stop the scheduler
    shutdown_scheduler()


app = FastAPI(
    title="Afya Guard API",
    version="0.1.0",
    description="API for Afya Guide application",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,  # Add the lifespan context manager
)

# CORS Configuration
origins = [
    "http://localhost:3000",
    "https://afya-guard-frontend.vercel.app",
]  # Add frontend domains here

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API Prefixes
BASE_URL_PREFIX = "/api/v1"
# Include API Routes
app.include_router(
    claim_router,
    prefix=f"{BASE_URL_PREFIX}",
    tags=["Claims"],
)
app.include_router(
    auth_router,
    prefix=f"{BASE_URL_PREFIX}",
    tags=["Users - Authentication"],
)
