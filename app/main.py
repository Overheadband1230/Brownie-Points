from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import DEFAULT_CATEGORIES, INVITE_CODE
from app.db import Base, SessionLocal, engine
from app.models import AppSetting, Category
from app.routers import api, pages
from app.services.settings import INVITE_CODE_KEY

# Columns added to existing tables after the initial release. create_all
# only creates missing *tables*, so these are applied via ALTER TABLE on
# boot when absent — keeping upgrades a plain "pull + rebuild" with no
# manual migration step. Append here; never edit or remove past entries.
_COLUMN_MIGRATIONS = [
    ("users", "avatar", "TEXT NOT NULL DEFAULT '🍫'"),
    ("redemptions", "nudged_at", "DATETIME"),
    ("ledger_entries", "bet_id", "INTEGER REFERENCES bets(id)"),
]


def _ensure_columns(conn) -> None:
    for table, column, ddl in _COLUMN_MIGRATIONS:
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    """Create tables if absent, apply column migrations, seed defaults.
    Safe to re-run on every boot."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _ensure_columns(conn)
    with SessionLocal() as db:
        existing = set(db.scalars(select(Category.name)).all())
        for name in DEFAULT_CATEGORIES:
            if name not in existing:
                db.add(Category(name=name))
        # Seed the invite code from the env var once; after that the
        # DB value wins and admins edit it from the Admin page.
        if db.get(AppSetting, INVITE_CODE_KEY) is None:
            db.add(AppSetting(key=INVITE_CODE_KEY, value=INVITE_CODE))
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Brownie Points 🍫", lifespan=lifespan)

app.include_router(api.router)
app.include_router(pages.router)


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    # Browser page requests get bounced to login; API calls get JSON.
    if request.url.path.startswith("/api"):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Not logged in."})
    return RedirectResponse("/login", status_code=303)
