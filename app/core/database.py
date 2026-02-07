from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

SQLALCHEMY_DATABASE_URL = settings.database_url

engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    # connect_args={"check_same_thread": False}
    echo=True,
    future=True,
    pool_size=20,
    max_overflow=20,
    pool_timeout=60,
)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
