"""Service functions for all ledger-affecting operations.

Each function performs its validations and inserts inside the caller's
session and commits, so the ledger stays consistent. Raises ValueError
with a user-facing message on validation failure and PermissionError
when the acting user is not allowed to perform the action.
"""

from app.models import (
    EntryType,
    Item,
    LedgerEntry,
    Redemption,
    RedemptionStatus,
    User,
    utcnow,
)
from app.services.ledger import get_balance

from sqlalchemy.orm import Session


def award(db: Session, from_user: User, to_user_id: int, amount: int,
          reason: str, category: str | None) -> LedgerEntry:
    if amount <= 0:
        raise ValueError("Amount must be a positive number of Brownie Points.")
    if to_user_id == from_user.id:
        raise ValueError("Nice try — you can't award points to yourself.")
    if db.get(User, to_user_id) is None:
        raise ValueError("That user doesn't exist.")
    if not reason.strip():
        raise ValueError("Every Brownie Point needs a reason. What did they do?")

    entry = LedgerEntry(
        user_id=to_user_id,
        counterparty_id=from_user.id,
        entry_type=EntryType.AWARD,
        amount=amount,
        reason=reason.strip(),
        category=category or None,
        created_by=from_user.id,
    )
    db.add(entry)
    db.commit()
    return entry


def create_redemption(db: Session, requester: User, grantor_id: int, amount: int,
                      reason: str, category: str | None) -> Redemption:
    if amount <= 0:
        raise ValueError("Amount must be a positive number of Brownie Points.")
    if grantor_id == requester.id:
        raise ValueError("You can't redeem points with yourself — go outside.")
    if db.get(User, grantor_id) is None:
        raise ValueError("That user doesn't exist.")
    if not reason.strip():
        raise ValueError("What favor do you want? Describe it.")

    balance = get_balance(db, requester.id)
    if balance.spendable < amount:
        raise ValueError(
            f"Not enough points: you have {balance.spendable} spendable, "
            f"but tried to spend {amount}."
        )

    redemption = Redemption(
        requester_id=requester.id,
        grantor_id=grantor_id,
        amount=amount,
        reason=reason.strip(),
        category=category or None,
        status=RedemptionStatus.PENDING,
    )
    db.add(redemption)
    db.flush()  # get redemption.id for the hold entry

    db.add(LedgerEntry(
        user_id=requester.id,
        counterparty_id=grantor_id,
        entry_type=EntryType.HOLD,
        amount=-amount,
        reason=redemption.reason,
        category=redemption.category,
        redemption_id=redemption.id,
        created_by=requester.id,
    ))
    db.commit()
    return redemption


def _get_redemption(db: Session, redemption_id: int) -> Redemption:
    redemption = db.get(Redemption, redemption_id)
    if redemption is None:
        raise ValueError("That redemption request doesn't exist.")
    return redemption


def approve_redemption(db: Session, actor: User, redemption_id: int) -> Redemption:
    redemption = _get_redemption(db, redemption_id)
    if actor.id != redemption.grantor_id:
        raise PermissionError("Only the person being asked can approve this.")
    if redemption.status != RedemptionStatus.PENDING:
        raise ValueError("Only pending requests can be approved.")
    redemption.status = RedemptionStatus.APPROVED
    redemption.resolved_at = utcnow()
    db.commit()
    return redemption


def fulfill_redemption(db: Session, actor: User, redemption_id: int) -> Redemption:
    redemption = _get_redemption(db, redemption_id)
    if actor.id not in (redemption.grantor_id, redemption.requester_id):
        raise PermissionError("Only the two parties involved can mark this fulfilled.")
    if redemption.status != RedemptionStatus.APPROVED:
        raise ValueError("Only approved requests can be marked fulfilled.")
    redemption.status = RedemptionStatus.FULFILLED
    redemption.resolved_at = utcnow()
    db.add(LedgerEntry(
        user_id=redemption.requester_id,
        counterparty_id=redemption.grantor_id,
        entry_type=EntryType.DEBIT,
        amount=-redemption.amount,
        reason=redemption.reason,
        category=redemption.category,
        redemption_id=redemption.id,
        created_by=actor.id,
    ))
    db.commit()
    return redemption


def _release(db: Session, actor: User, redemption: Redemption, new_status: str) -> Redemption:
    redemption.status = new_status
    redemption.resolved_at = utcnow()
    db.add(LedgerEntry(
        user_id=redemption.requester_id,
        counterparty_id=redemption.grantor_id,
        entry_type=EntryType.RELEASE,
        amount=redemption.amount,
        reason=redemption.reason,
        category=redemption.category,
        redemption_id=redemption.id,
        created_by=actor.id,
    ))
    db.commit()
    return redemption


def deny_redemption(db: Session, actor: User, redemption_id: int) -> Redemption:
    redemption = _get_redemption(db, redemption_id)
    if actor.id != redemption.grantor_id:
        raise PermissionError("Only the person being asked can deny this.")
    if redemption.status != RedemptionStatus.PENDING:
        raise ValueError("Only pending requests can be denied.")
    return _release(db, actor, redemption, RedemptionStatus.DENIED)


def cancel_redemption(db: Session, actor: User, redemption_id: int) -> Redemption:
    redemption = _get_redemption(db, redemption_id)
    if actor.id not in (redemption.requester_id, redemption.grantor_id):
        raise PermissionError("Only the two parties involved can cancel this.")
    if redemption.status != RedemptionStatus.PENDING:
        raise ValueError("Only pending requests can be cancelled.")
    return _release(db, actor, redemption, RedemptionStatus.CANCELLED)


def create_item(db: Session, owner: User, name: str, price: int,
                category: str | None) -> Item:
    if price <= 0:
        raise ValueError("Price must be a positive number of Brownie Points.")
    if not name.strip():
        raise ValueError("Your item needs a name. What are you offering?")

    item = Item(owner_id=owner.id, name=name.strip(), price=price,
                category=category or None)
    db.add(item)
    db.commit()
    return item


def delist_item(db: Session, actor: User, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise ValueError("That item doesn't exist.")
    if actor.id != item.owner_id and not actor.is_admin:
        raise PermissionError("Only the owner can take an item off the menu.")
    item.is_active = False
    db.commit()
    return item


def redeem_item(db: Session, requester: User, item_id: int) -> Redemption:
    """Redeem a listed item at its set price.

    Because the owner pre-agreed to the deal by listing it, the redemption
    is created already APPROVED — points go on hold as usual and the
    exchange still has to be marked fulfilled.
    """
    item = db.get(Item, item_id)
    if item is None or not item.is_active:
        raise ValueError("That item is no longer on the menu.")
    if item.owner_id == requester.id:
        raise ValueError("Redeeming your own item is just doing yourself a favor.")

    balance = get_balance(db, requester.id)
    if balance.spendable < item.price:
        raise ValueError(
            f"Not enough points: this costs {item.price} BP "
            f"but you only have {balance.spendable} spendable."
        )

    redemption = Redemption(
        requester_id=requester.id,
        grantor_id=item.owner_id,
        amount=item.price,
        reason=item.name,
        category=item.category,
        status=RedemptionStatus.APPROVED,
        resolved_at=utcnow(),
    )
    db.add(redemption)
    db.flush()

    db.add(LedgerEntry(
        user_id=requester.id,
        counterparty_id=item.owner_id,
        entry_type=EntryType.HOLD,
        amount=-item.price,
        reason=redemption.reason,
        category=redemption.category,
        redemption_id=redemption.id,
        created_by=requester.id,
    ))
    db.commit()
    return redemption


def adjustment(db: Session, admin: User, target_user_id: int, amount: int,
               reason: str) -> LedgerEntry:
    if not admin.is_admin:
        raise PermissionError("Only admins can make adjustments.")
    if amount == 0:
        raise ValueError("An adjustment of zero adjusts nothing.")
    if db.get(User, target_user_id) is None:
        raise ValueError("That user doesn't exist.")
    if not reason.strip():
        raise ValueError("Adjustments must carry a reason — the ledger remembers.")

    entry = LedgerEntry(
        user_id=target_user_id,
        counterparty_id=None,
        entry_type=EntryType.ADJUSTMENT,
        amount=amount,
        reason=reason.strip(),
        created_by=admin.id,
    )
    db.add(entry)
    db.commit()
    return entry
