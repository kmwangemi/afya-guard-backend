from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contextlib import asynccontextmanager
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from sqlalchemy.orm import selectinload

from src.api.v1.database import Base, engine
from src.api.v1.routes.claim_route import claim_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup: create database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Shutdown: drop database tables (optional)
    await engine.dispose()

app = FastAPI(lifespan=lifespan)

# CORS Configuration
origins = [
    "http://localhost:3000",
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
