import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import SECRET_KEY, SESSION_MAX_AGE_SECONDS
from app.db import get_db
from app.models import User

COOKIE_NAME = "brownie_session"

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="brownie-session")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def set_session_cookie(response: Response, user_id: int) -> None:
    token = _serializer.dumps({"user_id": user_id})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except BadSignature:
        return None
    return db.get(User, data.get("user_id"))


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not logged in.")
    return user


def register_user(db: Session, email: str, display_name: str, password: str) -> User:
    email = email.strip().lower()
    display_name = display_name.strip()
    if not email or "@" not in email:
        raise ValueError("That doesn't look like an email address.")
    if not display_name:
        raise ValueError("Pick a display name — it's what your friends will see.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise ValueError("That email is already registered. Try logging in.")

    # First registered user becomes admin.
    is_first = db.scalar(select(func.count(User.id))) == 0
    user = User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(password),
        is_admin=is_first,
    )
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not verify_password(password, user.password_hash):
        raise ValueError("Wrong email or password.")
    return user
