from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from src.api.v1.database import Base, engine
from src.api.v1.routes.claim_route import claim_router

Base.metadata.create_all(bind=engine)

app = FastAPI()

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
