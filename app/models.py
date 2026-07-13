from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None) -> str | None:
    """ISO string explicitly marked UTC (trailing Z).

    Timestamps are stored as UTC but come back from SQLite naive; a bare
    isoformat() would be parsed as *local* time by JS Date. Works for both
    naive (from DB) and aware (fresh utcnow()) values.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(String)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar: Mapped[str] = mapped_column(String, default="🍫", server_default="🍫")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RedemptionStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"
    FULFILLED = "FULFILLED"


class Redemption(Base):
    __tablename__ = "redemptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    grantor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default=RedemptionStatus.PENDING, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    grantor: Mapped[User] = relationship(foreign_keys=[grantor_id])


class EntryType:
    AWARD = "AWARD"
    HOLD = "HOLD"
    RELEASE = "RELEASE"
    DEBIT = "DEBIT"
    ADJUSTMENT = "ADJUSTMENT"


class BetStatus:
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    SETTLED = "SETTLED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"
    VOIDED = "VOIDED"


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenger_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    opponent_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    stake: Mapped[int] = mapped_column(Integer)  # per side; the pot is 2× this
    terms: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default=BetStatus.PROPOSED, index=True)
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    challenger: Mapped[User] = relationship(foreign_keys=[challenger_id])
    opponent: Mapped[User] = relationship(foreign_keys=[opponent_id])
    winner: Mapped[User | None] = relationship(foreign_keys=[winner_id])


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    entry_type: Mapped[str] = mapped_column(String, index=True)
    amount: Mapped[int] = mapped_column(Integer)  # signed: + credits, − debits/holds
    reason: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    redemption_id: Mapped[int | None] = mapped_column(ForeignKey("redemptions.id"), nullable=True)
    bet_id: Mapped[int | None] = mapped_column(ForeignKey("bets.id"), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    counterparty: Mapped[User | None] = relationship(foreign_keys=[counterparty_id])
    redemption: Mapped[Redemption | None] = relationship(foreign_keys=[redemption_id])
    bet: Mapped[Bet | None] = relationship(foreign_keys=[bet_id])


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Item(Base):
    """A redeemable offer listed by a user at a fixed price."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    price: Mapped[int] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)


class DailyAnswer(Base):
    __tablename__ = "daily_answers"
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[str] = mapped_column(String, index=True)  # "YYYY-MM-DD"
    answer: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class SealedGift(Base):
    """An award wrapped up with a note; points exist only once opened."""

    __tablename__ = "sealed_gifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text)
    unlock_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sender: Mapped[User] = relationship(foreign_keys=[sender_id])
    recipient: Mapped[User] = relationship(foreign_keys=[recipient_id])


class MemoryNote(Base):
    __tablename__ = "memory_notes"
    __table_args__ = (UniqueConstraint("kind", "ref_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String)  # 'redemption' | 'bet' | 'gift'
    ref_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class AppSetting(Base):
    """Runtime-editable settings (e.g. the invite code)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)
