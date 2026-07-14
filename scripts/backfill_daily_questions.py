"""One-off backfill: pin the questions for days answered before the
per-day pinning fix (commit 7420318) landed.

Those days have DailyAnswer rows but no DailyQuestion row, so the memory
wall was falling back to the old `days_since_epoch % len(bank)` formula —
which now returns the wrong text because the bank grew. This writes the
question that was *actually* shown on each day, which also marks it
"used" so `question_for` won't hand it out again.

Idempotent: re-running only fixes rows that are missing or wrong.

Run it wherever the DB lives, e.g. inside the container:
    docker compose exec web python scripts/backfill_daily_questions.py
or locally against ./brownie.db:
    python scripts/backfill_daily_questions.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import DailyQuestion
from app.services.daily import QUESTION_BANK


def _pick(substr: str) -> str:
    """Exact bank text via a unique substring — avoids retyping curly
    apostrophes and guarantees the pin matches a real bank entry."""
    matches = [q for q in QUESTION_BANK if substr in q]
    if len(matches) != 1:
        raise SystemExit(f"{substr!r} matched {len(matches)} bank entries, expected 1")
    return matches[0]


# day -> the question actually shown that day (13th was on the 42-question
# bank, 14th on the 151-question bank after the overnight deploy).
BACKFILL = {
    "2026-07-13": _pick("horse-sized duck"),
    "2026-07-14": _pick("procrastinate"),
}


def main() -> None:
    db = SessionLocal()
    try:
        for day, question in BACKFILL.items():
            row = db.get(DailyQuestion, day)
            if row is None:
                db.add(DailyQuestion(day=day, question=question))
                print(f"  + pinned {day} -> {question!r}")
            elif row.question != question:
                print(f"  ~ {day}: {row.question!r} -> {question!r}")
                row.question = question
            else:
                print(f"  = {day} already correct")
        db.commit()
        print("Done.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
