"""Runtime-editable app settings, stored in the app_settings table.

The invite code is seeded from the INVITE_CODE env var on first boot and
editable by admins afterwards — the DB value wins once it exists.
"""

from sqlalchemy.orm import Session

from app.config import INVITE_CODE as DEFAULT_INVITE_CODE
from app.models import AppSetting

INVITE_CODE_KEY = "invite_code"


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(AppSetting, key)
    return row.value if row is not None else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def get_invite_code(db: Session) -> str:
    return get_setting(db, INVITE_CODE_KEY, DEFAULT_INVITE_CODE)


def set_invite_code(db: Session, code: str) -> None:
    set_setting(db, INVITE_CODE_KEY, code)
