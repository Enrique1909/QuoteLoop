import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine


def _load_env_local() -> None:
    """Local dev: read .env.local written by `neon env pull` (real env vars take precedence)."""
    path = Path(__file__).resolve().parent.parent / ".env.local"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_local()


def _normalise(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _make_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    # Neon pooled connections go through PgBouncer (transaction mode): disable psycopg's
    # automatic server-side prepared statements, and drop dead idle connections after scale-to-zero.
    return create_engine(url, connect_args={"prepare_threshold": None}, pool_pre_ping=True, pool_recycle=300)


# App traffic: pooled URL (DATABASE_URL). Schema setup: direct URL (DATABASE_URL_UNPOOLED), per Neon guidance.
DATABASE_URL = _normalise(os.environ.get("DATABASE_URL", "sqlite:///./clientroom.db"))
DIRECT_DATABASE_URL = _normalise(os.environ.get("DATABASE_URL_UNPOOLED", "")) or DATABASE_URL

engine = _make_engine(DATABASE_URL)


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)
    ddl_engine = engine if DIRECT_DATABASE_URL == DATABASE_URL else _make_engine(DIRECT_DATABASE_URL)
    SQLModel.metadata.create_all(ddl_engine)
    if ddl_engine is not engine:
        ddl_engine.dispose()


def get_session():
    return Session(engine)
