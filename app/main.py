from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import DEFAULT_CATEGORIES
from app.db import Base, SessionLocal, engine
from app.models import Category  # noqa: F401 — ensure all models are registered
from app.routers import api, pages


def init_db() -> None:
    """Create tables if absent and seed default categories. Safe to re-run."""
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        existing = set(db.scalars(select(Category.name)).all())
        for name in DEFAULT_CATEGORIES:
            if name not in existing:
                db.add(Category(name=name))
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
