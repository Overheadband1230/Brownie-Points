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
    "Coffee or tea — and how do you take it?",
    "What’s a hobby you wish you were good at?",
    "If you could master one skill instantly, what would it be?",
    "What’s your go-to late-night snack?",
    "Sunrise or sunset?",
    "What’s your favorite app on your phone right now?",
    "What’s a job you’d be terrible at?",
    "What’s your guilty pleasure TV show?",
    "If you had a personal theme song, what would it be?",
    "What’s something you believed way too long as a kid?",
    "What’s your favorite fast food order?",
    "What’s a place you’ve always wanted to visit?",
    "What’s your biggest pet peeve?",
    "What’s your weirdest habit?",
    "What’s a small purchase that improved your life?",
    "What’s your favorite holiday and why?",
    "What’s your favorite way to waste time?",
    "What’s a food combo people judge you for?",
    "What’s your least favorite chore?",
    "What’s your favorite season?",
    "What’s one thing you can’t live without?",
    "What’s your dream car?",
    "What’s your go-to comfort food?",
    "What’s something you’ve always wanted to learn?",
    "What’s your favorite board game?",
    "What’s a show you quit halfway through?",
    "What’s your biggest irrational annoyance?",
    "What’s your favorite childhood memory?",
    "What’s your favorite dessert?",
    "What’s your least favorite social situation?",
    "What’s your favorite day of the week?",
    "What’s a song you know every word to?",
    "What’s your biggest fear that’s actually realistic?",
    "What’s your favorite ice cream flavor?",
    "What’s your least favorite food texture?",
    "What’s your go-to outfit?",
    "What’s something you collect (or would collect)?",
    "What’s your favorite thing to do alone?",
    "What’s your favorite thing to do with friends?",
    "What’s your favorite smell?",
    "What’s your least favorite smell?",
    "What’s a movie you regret watching?",
    "What’s your favorite kind of weather?",
    "What’s your worst travel experience?",
    "What’s your favorite pizza chain?",
    "What’s your biggest time-waster?",
    "What’s your favorite sport to watch?",
    "What’s your favorite sport to play?",
    "What’s your biggest flex?",
    "What’s something you’re proud of but rarely talk about?",
    "What’s your favorite candy?",
    "What’s your least favorite drink?",
    "What’s your go-to excuse when you don’t want to go out?",
    "What’s your favorite YouTube channel?",
    "What’s your favorite way to relax?",
    "What’s a trend you actually liked?",
    "What’s your least favorite holiday?",
    "What’s your favorite kind of sandwich?",
    "What’s something you wish you could unsee?",
    "What’s your biggest “I told you so” moment?",
    "What’s your favorite type of music to drive to?",
    "What’s your go-to breakfast?",
    "What’s your least favorite color?",
    "What’s your favorite color combo?",
    "What’s your favorite kind of day (describe it)?",
    "What’s your most used emoji?",
    "What’s your favorite kind of joke?",
    "What’s your worst habit?",
    "What’s your favorite phone game?",
    "What’s something you do better than most people?",
    "What’s something you always procrastinate on?",
    "What’s your favorite fruit?",
    "What’s your least favorite vegetable?",
    "What’s your favorite restaurant?",
    "What’s your favorite thing to cook?",
    "What’s your worst cooking fail?",
    "What’s your favorite holiday food?",
    "What’s your go-to road trip snack?",
    "What’s your favorite thing about where you live?",
    "What’s your least favorite thing about where you live?",
    "What’s your favorite memory with friends?",
    "What’s your favorite way to spend a weekend?",
    "What’s your biggest motivation right now?",
    "What’s your favorite kind of content (memes, podcasts, etc.)?",
    "What’s your least favorite social media?",
    "What’s your favorite emoji combo?",
    "What’s your go-to drink order?",
    "What’s your favorite fast casual restaurant?",
    "What’s your favorite comfort movie?",
    "What’s your favorite thing to binge-watch?",
    "What’s your favorite holiday tradition?",
    "What’s your least favorite school subject ever?",
    "What’s your favorite school subject ever?",
    "What’s your favorite way to stay active?",
    "What’s your least favorite workout?",
    "What’s your favorite way to celebrate something?",
    "What’s your biggest current goal?",
    "What’s something you wish people asked you more about?",
    "What’s your favorite inside joke you’re part of?",
    "What’s one thing you find instantly attractive in someone?",
    "What’s your biggest turn-off on a first date?",
    "What’s your ideal date night look like?",
    "Do you prefer making the first move or being approached?",
    "What’s your love language?",
    "What’s something subtle someone can do that you find really attractive?",
    "Are you more into looks or personality first?",
    "What’s your biggest green flag in a person?",
    "What kind of flirting actually works on you?",
    "What’s something that gives you butterflies every time?"
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
