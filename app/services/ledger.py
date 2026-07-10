"""Balance derivation from the append-only ledger.

Convention (the spec's "recommended simplest model"):
- A HOLD reduces spendable balance only while its linked redemption is
  PENDING or APPROVED.
- On FULFILLED a DEBIT is inserted and the HOLD stops counting, so holds
  and debits never overlap.
- On DENIED/CANCELLED a RELEASE is inserted and the HOLD stops counting
  (the RELEASE itself is informational; it is not summed, to avoid
  double-counting with the now-inactive HOLD).

Balances are always derived from the ledger, never stored.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EntryType, LedgerEntry, Redemption, RedemptionStatus

ACTIVE_HOLD_STATUSES = (RedemptionStatus.PENDING, RedemptionStatus.APPROVED)


@dataclass
class Balance:
    awarded_in: int
    adjustments: int
    held: int      # positive magnitude of active holds
    spent: int     # positive magnitude of debits
    spendable: int
    lifetime_earned: int


def _sum(db: Session, user_id: int, entry_type: str) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.user_id == user_id, LedgerEntry.entry_type == entry_type
        )
    )


def get_balance(db: Session, user_id: int) -> Balance:
    awarded_in = _sum(db, user_id, EntryType.AWARD)
    adjustments = _sum(db, user_id, EntryType.ADJUSTMENT)
    spent = -_sum(db, user_id, EntryType.DEBIT)

    held = -(
        db.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0))
            .join(Redemption, LedgerEntry.redemption_id == Redemption.id)
            .where(
                LedgerEntry.user_id == user_id,
                LedgerEntry.entry_type == EntryType.HOLD,
                Redemption.status.in_(ACTIVE_HOLD_STATUSES),
            )
        )
    )

    spendable = awarded_in + adjustments - spent - held
    lifetime_earned = awarded_in + max(adjustments, 0)
    return Balance(
        awarded_in=awarded_in,
        adjustments=adjustments,
        held=held,
        spent=spent,
        spendable=spendable,
        lifetime_earned=lifetime_earned,
    )
