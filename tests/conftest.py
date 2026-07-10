import os
import tempfile

# Point the app at a throwaway SQLite file before anything imports app.db.
_tmpdir = tempfile.mkdtemp(prefix="brownie-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_tmpdir, 'test.db')}"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["INVITE_CODE"] = "test-invite"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app, init_db


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
