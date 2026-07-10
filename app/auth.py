import bcrypt
from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import INVITE_CODE, SECRET_KEY, SESSION_MAX_AGE_SECONDS
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


def _validate_email(db: Session, email: str, exclude_user_id: int | None = None) -> str:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("That doesn't look like an email address.")
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None and existing.id != exclude_user_id:
        raise ValueError("That email is already registered.")
    return email


def register_user(db: Session, email: str, display_name: str, password: str,
                  invite_code: str = "") -> User:
    if invite_code.strip() != INVITE_CODE:
        raise ValueError("Wrong invite code. No brownies for strangers. 🍫")
    email = _validate_email(db, email)
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("Pick a display name — it's what your friends will see.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")

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


def update_account(db: Session, user: User, email: str, display_name: str,
                   current_password: str) -> User:
    if not verify_password(current_password, user.password_hash):
        raise ValueError("Current password is incorrect.")
    email = _validate_email(db, email, exclude_user_id=user.id)
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("Display name can't be empty.")
    user.email = email
    user.display_name = display_name
    db.commit()
    return user


def change_password(db: Session, user: User, current_password: str,
                    new_password: str) -> User:
    if not verify_password(current_password, user.password_hash):
        raise ValueError("Current password is incorrect.")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")
    user.password_hash = hash_password(new_password)
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not verify_password(password, user.password_hash):
        raise ValueError("Wrong email or password.")
    return user
