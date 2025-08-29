from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
import os

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable must be set!")

# Tune DB pool for high concurrency; overridable via env
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "40"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "60"))
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT,
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()
