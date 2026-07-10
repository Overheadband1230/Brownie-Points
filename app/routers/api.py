from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import auth
from app.db import get_db
from app.models import Category, EntryType, Item, LedgerEntry, Redemption, User
from app.services import transactions
from app.services.ledger import get_balance

router = APIRouter(prefix="/api")


def _handle(fn, *args, **kwargs):
    """Translate service-layer errors into HTTP responses."""
    try:
        return fn(*args, **kwargs)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- auth ----------

class RegisterIn(BaseModel):
    email: str
    display_name: str
    password: str
    invite_code: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/register")
def api_register(body: RegisterIn, response: Response, db: Session = Depends(get_db)):
    user = _handle(auth.register_user, db, body.email, body.display_name,
                   body.password, body.invite_code)
    auth.set_session_cookie(response, user.id)
    return {"id": user.id, "display_name": user.display_name, "is_admin": user.is_admin}


@router.post("/auth/login")
def api_login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = _handle(auth.authenticate, db, body.email, body.password)
    auth.set_session_cookie(response, user.id)
    return {"id": user.id, "display_name": user.display_name, "is_admin": user.is_admin}


@router.post("/auth/logout")
def api_logout(response: Response):
    auth.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def api_me(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    balance = get_balance(db, user.id)
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "spendable_balance": balance.spendable,
        "lifetime_earned": balance.lifetime_earned,
        "held": balance.held,
    }


class AccountIn(BaseModel):
    email: str
    display_name: str
    current_password: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/me/account")
def api_update_account(body: AccountIn, user: User = Depends(auth.get_current_user),
                       db: Session = Depends(get_db)):
    u = _handle(auth.update_account, db, user, body.email, body.display_name,
                body.current_password)
    return {"id": u.id, "email": u.email, "display_name": u.display_name}


@router.post("/me/password")
def api_change_password(body: PasswordIn, user: User = Depends(auth.get_current_user),
                        db: Session = Depends(get_db)):
    _handle(auth.change_password, db, user, body.current_password, body.new_password)
    return {"ok": True}


# ---------- users ----------

@router.get("/users")
def api_users(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.display_name)).all()
    return [
        {
            "id": u.id,
            "display_name": u.display_name,
            "spendable_balance": get_balance(db, u.id).spendable,
        }
        for u in users
    ]


# ---------- awards ----------

class AwardIn(BaseModel):
    to_user_id: int
    amount: int
    reason: str
    category: str | None = None


@router.post("/awards", status_code=201)
def api_award(body: AwardIn, user: User = Depends(auth.get_current_user),
              db: Session = Depends(get_db)):
    entry = _handle(transactions.award, db, user, body.to_user_id, body.amount,
                    body.reason, body.category)
    return _entry_json(entry)


@router.get("/awards")
def api_awards(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    entries = db.scalars(
        select(LedgerEntry)
        .where(
            LedgerEntry.entry_type == EntryType.AWARD,
            or_(LedgerEntry.user_id == user.id, LedgerEntry.counterparty_id == user.id),
        )
        .order_by(LedgerEntry.created_at.desc())
    ).all()
    return [_entry_json(e) for e in entries]


# ---------- redemptions ----------

class RedemptionIn(BaseModel):
    grantor_id: int
    amount: int
    reason: str
    category: str | None = None


def _redemption_json(r: Redemption) -> dict:
    return {
        "id": r.id,
        "requester_id": r.requester_id,
        "grantor_id": r.grantor_id,
        "amount": r.amount,
        "reason": r.reason,
        "category": r.category,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
    }


@router.post("/redemptions", status_code=201)
def api_create_redemption(body: RedemptionIn, user: User = Depends(auth.get_current_user),
                          db: Session = Depends(get_db)):
    r = _handle(transactions.create_redemption, db, user, body.grantor_id,
                body.amount, body.reason, body.category)
    return _redemption_json(r)


@router.get("/redemptions")
def api_redemptions(status: str | None = None,
                    user: User = Depends(auth.get_current_user),
                    db: Session = Depends(get_db)):
    q = select(Redemption).where(
        or_(Redemption.requester_id == user.id, Redemption.grantor_id == user.id)
    )
    if status:
        q = q.where(Redemption.status == status.upper())
    rows = db.scalars(q.order_by(Redemption.created_at.desc())).all()
    return [_redemption_json(r) for r in rows]


@router.post("/redemptions/{redemption_id}/approve")
def api_approve(redemption_id: int, user: User = Depends(auth.get_current_user),
                db: Session = Depends(get_db)):
    return _redemption_json(_handle(transactions.approve_redemption, db, user, redemption_id))


@router.post("/redemptions/{redemption_id}/deny")
def api_deny(redemption_id: int, user: User = Depends(auth.get_current_user),
             db: Session = Depends(get_db)):
    return _redemption_json(_handle(transactions.deny_redemption, db, user, redemption_id))


@router.post("/redemptions/{redemption_id}/cancel")
def api_cancel(redemption_id: int, user: User = Depends(auth.get_current_user),
               db: Session = Depends(get_db)):
    return _redemption_json(_handle(transactions.cancel_redemption, db, user, redemption_id))


@router.post("/redemptions/{redemption_id}/fulfill")
def api_fulfill(redemption_id: int, user: User = Depends(auth.get_current_user),
                db: Session = Depends(get_db)):
    return _redemption_json(_handle(transactions.fulfill_redemption, db, user, redemption_id))


# ---------- items ----------

class ItemIn(BaseModel):
    name: str
    price: int
    category: str | None = None


def _item_json(i: Item) -> dict:
    return {
        "id": i.id,
        "owner_id": i.owner_id,
        "owner_name": i.owner.display_name,
        "name": i.name,
        "price": i.price,
        "category": i.category,
        "is_active": i.is_active,
    }


@router.post("/items", status_code=201)
def api_create_item(body: ItemIn, user: User = Depends(auth.get_current_user),
                    db: Session = Depends(get_db)):
    return _item_json(_handle(transactions.create_item, db, user, body.name,
                              body.price, body.category))


@router.get("/items")
def api_items(user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(Item).where(Item.is_active == True)  # noqa: E712
        .order_by(Item.owner_id, Item.price)
    ).all()
    return [_item_json(i) for i in items]


@router.post("/items/{item_id}/delist")
def api_delist_item(item_id: int, user: User = Depends(auth.get_current_user),
                    db: Session = Depends(get_db)):
    return _item_json(_handle(transactions.delist_item, db, user, item_id))


@router.post("/items/{item_id}/redeem", status_code=201)
def api_redeem_item(item_id: int, user: User = Depends(auth.get_current_user),
                    db: Session = Depends(get_db)):
    return _redemption_json(_handle(transactions.redeem_item, db, user, item_id))


# ---------- ledger ----------

def _entry_json(e: LedgerEntry) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "counterparty_id": e.counterparty_id,
        "entry_type": e.entry_type,
        "amount": e.amount,
        "reason": e.reason,
        "category": e.category,
        "redemption_id": e.redemption_id,
        "created_by": e.created_by,
        "created_at": e.created_at.isoformat(),
    }


@router.get("/ledger")
def api_ledger(page: int = 1, page_size: int = 25,
               user: User = Depends(auth.get_current_user),
               db: Session = Depends(get_db)):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    entries = db.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.user_id == user.id)
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {"page": page, "entries": [_entry_json(e) for e in entries]}


# ---------- reports ----------

@router.get("/reports/summary")
def api_reports_summary(user: User = Depends(auth.get_current_user),
                        db: Session = Depends(get_db)):
    balance = get_balance(db, user.id)

    entries = db.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.user_id == user.id)
        .order_by(LedgerEntry.created_at, LedgerEntry.id)
    ).all()

    # Balance over time: spendable balance after each entry. DEBIT entries
    # are skipped because every DEBIT recharacterizes a HOLD that is already
    # counted in the running sum (no RELEASE is written on fulfillment);
    # counting both would double-subtract the spend. RELEASE entries do
    # count — they cancel the HOLD of a denied/cancelled request.
    points_over_time = []
    running = 0
    for e in entries:
        if e.entry_type == EntryType.DEBIT:
            continue
        running += e.amount
        points_over_time.append({"at": e.created_at.isoformat(), "balance": running})

    # Points by category (awards received).
    by_category: dict[str, int] = {}
    for e in entries:
        if e.entry_type == EntryType.AWARD:
            key = e.category or "uncategorized"
            by_category[key] = by_category.get(key, 0) + e.amount

    # Who-owes-whom: net BP per counterparty (awards received minus fulfilled
    # debits paid back to them).
    net_by_counterparty: dict[int, int] = {}
    for e in entries:
        if e.counterparty_id is None:
            continue
        if e.entry_type in (EntryType.AWARD, EntryType.DEBIT):
            net_by_counterparty[e.counterparty_id] = (
                net_by_counterparty.get(e.counterparty_id, 0) + e.amount
            )
    counterparties = []
    for uid, net in sorted(net_by_counterparty.items(), key=lambda kv: -kv[1]):
        other = db.get(User, uid)
        counterparties.append({
            "user_id": uid,
            "display_name": other.display_name if other else "?",
            "net": net,
        })

    return {
        "totals": {
            "awarded": balance.awarded_in,
            "adjustments": balance.adjustments,
            "spent": balance.spent,
            "held": balance.held,
            "spendable": balance.spendable,
            "lifetime_earned": balance.lifetime_earned,
        },
        "points_over_time": points_over_time,
        "by_category": by_category,
        "net_by_counterparty": counterparties,
    }


# ---------- admin ----------

class AdjustmentIn(BaseModel):
    user_id: int
    amount: int
    reason: str


@router.post("/adjustments", status_code=201)
def api_adjustment(body: AdjustmentIn, user: User = Depends(auth.get_current_user),
                   db: Session = Depends(get_db)):
    entry = _handle(transactions.adjustment, db, user, body.user_id, body.amount, body.reason)
    return _entry_json(entry)


# ---------- categories ----------

@router.get("/categories")
def api_categories(db: Session = Depends(get_db)):
    return [c.name for c in db.scalars(select(Category).order_by(Category.id)).all()]
