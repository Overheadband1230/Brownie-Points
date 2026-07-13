"""The memory wall: a timeline of things that actually happened —
fulfilled favors, settled bets, opened gifts, and days you both
answered the question. Accidentally a scrapbook.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Bet,
    BetStatus,
    DailyAnswer,
    MemoryNote,
    Redemption,
    RedemptionStatus,
    SealedGift,
    User,
)
from app.services.daily import todays_question

VALID_KINDS = ("redemption", "bet", "gift")


@dataclass
class MemoryItem:
    kind: str          # 'redemption' | 'bet' | 'gift' | 'qa'
    at: datetime
    obj: object        # the source row ('qa' → list[DailyAnswer])
    ref_id: int | None = None
    question: str | None = None
    notes: list = field(default_factory=list)


def timeline(db: Session, limit: int = 100) -> list[MemoryItem]:
    items: list[MemoryItem] = []

    for r in db.scalars(select(Redemption).where(
            Redemption.status == RedemptionStatus.FULFILLED)).all():
        items.append(MemoryItem("redemption", r.resolved_at or r.created_at, r, r.id))

    for b in db.scalars(select(Bet).where(Bet.status == BetStatus.SETTLED)).all():
        items.append(MemoryItem("bet", b.resolved_at or b.created_at, b, b.id))

    for g in db.scalars(select(SealedGift).where(SealedGift.opened_at.is_not(None))).all():
        items.append(MemoryItem("gift", g.opened_at, g, g.id))

    # Q&A days where at least two people answered.
    answers = db.scalars(select(DailyAnswer).order_by(DailyAnswer.created_at)).all()
    by_day: dict[str, list[DailyAnswer]] = {}
    for a in answers:
        by_day.setdefault(a.day, []).append(a)
    for day, rows in by_day.items():
        if len(rows) >= 2:
            items.append(MemoryItem("qa", max(r.created_at for r in rows), rows,
                                    question=todays_question(day)))

    items.sort(key=lambda i: i.at, reverse=True)
    items = items[:limit]

    # Attach notes to notable items.
    notes = db.scalars(select(MemoryNote).order_by(MemoryNote.created_at)).all()
    by_key: dict[tuple, list[MemoryNote]] = {}
    for n in notes:
        by_key.setdefault((n.kind, n.ref_id), []).append(n)
    for item in items:
        if item.ref_id is not None:
            item.notes = by_key.get((item.kind, item.ref_id), [])
    return items


def upsert_note(db: Session, user: User, kind: str, ref_id: int, note: str) -> MemoryNote:
    if kind not in VALID_KINDS:
        raise ValueError("You can't annotate that.")
    note = note.strip()
    if not note:
        raise ValueError("A memory note needs words.")
    model = {"redemption": Redemption, "bet": Bet, "gift": SealedGift}[kind]
    if db.get(model, ref_id) is None:
        raise ValueError("That memory doesn't exist.")

    existing = db.scalar(select(MemoryNote).where(
        MemoryNote.kind == kind, MemoryNote.ref_id == ref_id,
        MemoryNote.user_id == user.id))
    if existing is not None:
        existing.note = note
        db.commit()
        return existing
    row = MemoryNote(kind=kind, ref_id=ref_id, user_id=user.id, note=note)
    db.add(row)
    db.commit()
    return row
