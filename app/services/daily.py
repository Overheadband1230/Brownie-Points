"""The daily question: both of you answer, answers reveal once you have.

Today's question is picked deterministically from the bank — no cron,
no scheduling. The day rolls at UTC midnight shifted by
QUESTION_UTC_OFFSET hours (default −5, ≈ midnight Eastern).
"""

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DailyAnswer, EntryType, LedgerEntry, User
from app.services.notify import notify

QUESTION_UTC_OFFSET = int(os.environ.get("QUESTION_UTC_OFFSET", "-5"))

QUESTION_BANK = [
    "Window seat or aisle seat?",
    "What's the most overrated movie of all time?",
    "Pancakes or waffles — defend your answer.",
    "What's a food you'll never eat again?",
    "Beach vacation or mountain cabin?",
    "What's your most useless talent?",
    "If you could only listen to one artist forever, who?",
    "Early bird or night owl — and is it a choice?",
    "What's the best thing you've ever eaten?",
    "Which fictional character would you want as a roommate?",
    "What's a hill you'll die on that nobody agrees with?",
    "Cold pizza for breakfast: yes or crime?",
    "What's your go-to karaoke song?",
    "If you won the lottery, what's the first dumb thing you'd buy?",
    "What's a smell that instantly takes you back somewhere?",
    "Sweet or salty — pick a side, no 'both'.",
    "What's the worst haircut you've ever had?",
    "Which superpower is secretly the worst deal?",
    "What's a movie you can quote way too much of?",
    "Road trip: driver or DJ?",
    "What's your comfort show you've rewatched the most?",
    "Cereal before milk or milk before cereal — confess.",
    "What's the pettiest thing you've ever done?",
    "If animals could talk, which would be the rudest?",
    "What's your most controversial pizza topping?",
    "Would you rather fight one horse-sized duck or 100 duck-sized horses?",
    "What's a song that always puts you in a good mood?",
    "What was your first screen name / email address?",
    "Time travel: 100 years forward or 100 years back?",
    "What's the best gift you've ever received?",
    "What's your irrational fear?",
    "If you had to eat one cuisine forever, which?",
    "What's a trend you fell for that you regret?",
    "Books: physical, e-reader, or audiobook?",
    "What's the most spontaneous thing you've ever done?",
    "Which decade had the best music?",
    "What would your last meal be?",
    "What's your unpopular breakfast opinion?",
    "If you opened a tiny shop, what would it sell?",
    "What's something small that makes your whole day better?",
    "Cats or dogs — and what does that say about you?",
    "What's the worst advice you've ever been given?",
]


def today_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    shifted = now + timedelta(hours=QUESTION_UTC_OFFSET)
    return shifted.strftime("%Y-%m-%d")


def todays_question(day: str | None = None) -> str:
    day = day or today_key()
    days_since_epoch = (datetime.strptime(day, "%Y-%m-%d") - datetime(1970, 1, 1)).days
    return QUESTION_BANK[days_since_epoch % len(QUESTION_BANK)]


def answers_for(db: Session, day: str | None = None) -> list[DailyAnswer]:
    day = day or today_key()
    return db.scalars(
        select(DailyAnswer).where(DailyAnswer.day == day).order_by(DailyAnswer.created_at)
    ).all()


def answer_today(db: Session, user: User, text: str) -> DailyAnswer:
    text = text.strip()
    if not text:
        raise ValueError("An answer needs actual words.")
    day = today_key()
    existing = db.scalar(
        select(DailyAnswer).where(DailyAnswer.user_id == user.id, DailyAnswer.day == day)
    )
    if existing is not None:
        raise ValueError("You already answered today — no take-backs.")

    row = DailyAnswer(user_id=user.id, day=day, answer=text)
    db.add(row)
    # Answering earns a point — the economy feeds itself.
    db.add(LedgerEntry(
        user_id=user.id,
        counterparty_id=None,
        entry_type=EntryType.AWARD,
        amount=1,
        reason="Answered the daily question 💌",
        category="other",
        created_by=user.id,
    ))
    others = db.scalars(select(User).where(User.id != user.id)).all()
    answered_ids = {a.user_id for a in answers_for(db, day)}
    for other in others:
        if other.id not in answered_ids:
            notify(db, other.id,
                   f"💌 {user.display_name} answered today's question — your turn!", "/")
    db.commit()
    return row
