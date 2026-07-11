"""In-app notifications.

notify() only adds to the session — callers commit, so the notification
lands atomically with the event that caused it.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Notification, utcnow


def notify(db: Session, user_id: int, text: str, link: str | None = None) -> None:
    db.add(Notification(user_id=user_id, text=text, link=link))


def unread_count(db: Session, user_id: int) -> int:
    return db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
    )


def list_and_mark_read(db: Session, user_id: int, limit: int = 50) -> list[Notification]:
    rows = db.scalars(
        select(Notification).where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ).all()
    now = utcnow()
    dirty = False
    for n in rows:
        # Transient flag for the template: highlight what was new this visit.
        n.was_unread = n.read_at is None
        if n.read_at is None:
            n.read_at = now
            dirty = True
    if dirty:
        db.commit()
    return rows
