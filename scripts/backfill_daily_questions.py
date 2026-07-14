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

# day -> the question actually shown that day. Edit if a value is wrong.
BACKFILL = {
    "2026-07-13": "Would you rather fight one horse-sized duck or 100 duck-sized horses?",
    "2026-07-14": "What's a song that always puts you in a good mood?",
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
