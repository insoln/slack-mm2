from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import StaticPool
import os

# Choose DATABASE_URL; default to in-memory SQLite (async) for tests when not provided
DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///:memory:"

# Tune DB pool for high concurrency; overridable via env
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "20"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "40"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "60"))

url = make_url(DATABASE_URL)
engine_kwargs: dict = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,
}

# SQLite (aiosqlite) requires special pooling and no size/overflow params
if url.drivername.startswith("sqlite+"):
    engine_kwargs.update({
        "poolclass": StaticPool,
    })
else:
    engine_kwargs.update({
        "pool_size": POOL_SIZE,
        "max_overflow": MAX_OVERFLOW,
        "pool_timeout": POOL_TIMEOUT,
    })

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()
