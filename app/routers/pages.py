from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import auth
from app.db import get_db
from app.models import (BetStatus, Category, EntryType, Item, LedgerEntry,
                        Redemption, RedemptionStatus, SealedGift, User, iso_utc)
from app.services import admin as admin_service
from app.services import bets as bets_service
from app.services import daily as daily_service
from app.services import memories as memories_service
from app.services import notify as notify_service
from app.services import transactions
from app.services.ledger import get_balance
from app.services.settings import get_invite_code

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.globals["iso_utc"] = iso_utc

PAGE_SIZE = 25


def _categories(db: Session) -> list[str]:
    return [c.name for c in db.scalars(select(Category).order_by(Category.id)).all()]


def _users_except(db: Session, user_id: int) -> list[User]:
    return db.scalars(
        select(User).where(User.id != user_id).order_by(User.display_name)
    ).all()


def _pending_for_me(db: Session, user: User) -> list[Redemption]:
    return db.scalars(
        select(Redemption)
        .where(Redemption.grantor_id == user.id,
               Redemption.status == RedemptionStatus.PENDING)
        .order_by(Redemption.created_at.desc())
    ).all()


def _render(request: Request, name: str, user: User | None, db: Session | None, **ctx):
    base = {"request": request, "user": user}
    if user is not None and db is not None:
        base["balance"] = get_balance(db, user.id)
        base["pending_count"] = len(_pending_for_me(db, user))
        base["unread_count"] = notify_service.unread_count(db, user.id)
    return templates.TemplateResponse(request, name, {**base, **ctx})


# ---------- auth pages ----------

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(auth.get_optional_user)):
    if user:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request, "user": None})


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...),
                 db: Session = Depends(get_db)):
    try:
        user = auth.authenticate(db, email, password)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "login.html",
            {"request": request, "user": None, "login_error": str(e), "email": email},
            status_code=400,
        )
    response = RedirectResponse("/", status_code=303)
    auth.set_session_cookie(response, user.id)
    return response


@router.post("/register")
def register_submit(request: Request, email: str = Form(...), display_name: str = Form(...),
                    password: str = Form(...), invite_code: str = Form(""),
                    db: Session = Depends(get_db)):
    try:
        user = auth.register_user(db, email, display_name, password, invite_code)
    except ValueError as e:
        return templates.TemplateResponse(
            request, "login.html",
            {"request": request, "user": None, "register_error": str(e),
             "email": email, "display_name": display_name, "tab": "register"},
            status_code=400,
        )
    response = RedirectResponse("/", status_code=303)
    auth.set_session_cookie(response, user.id)
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(response)
    return response


# ---------- dashboard ----------

def _daily_context(db: Session, user: User) -> dict:
    day = daily_service.today_key()
    answers = daily_service.answers_for(db, day)
    my_answer = next((a for a in answers if a.user_id == user.id), None)
    return {
        "daily_question": daily_service.question_for(db, day),
        "my_answer": my_answer,
        # No peeking: others' answers only visible once you've answered.
        "other_answers": [a for a in answers if a.user_id != user.id] if my_answer else [],
    }


def _gifts_context(db: Session, user: User) -> dict:
    unopened = db.scalars(
        select(SealedGift)
        .where(SealedGift.recipient_id == user.id, SealedGift.opened_at.is_(None))
        .order_by(SealedGift.created_at)
    ).all()
    now = transactions._as_naive_utc(transactions.utcnow())
    for g in unopened:
        unlock = transactions._as_naive_utc(g.unlock_at)
        g.openable = unlock is None or now >= unlock
    return {"sealed_gifts": unopened}


def _dashboard_render(request: Request, db: Session, user: User, **extra):
    recent = db.scalars(
        select(LedgerEntry)
        .where(or_(LedgerEntry.user_id == user.id, LedgerEntry.counterparty_id == user.id))
        .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
        .limit(10)
    ).all()

    # My outgoing requests still in flight (waiting on someone else).
    my_open_requests = db.scalars(
        select(Redemption)
        .where(Redemption.requester_id == user.id,
               Redemption.status.in_([RedemptionStatus.PENDING, RedemptionStatus.APPROVED]))
        .order_by(Redemption.created_at.desc())
    ).all()
    for r in my_open_requests:
        if r.status == RedemptionStatus.APPROVED:
            r.waiting_days = transactions.waiting_days(r)

    # Confetti when new awards arrived since this browser last looked.
    # Tracked with a plain cookie so it's zero-migration and per-device.
    try:
        seen_award_id = int(request.cookies.get("bp_seen_award", "0"))
    except ValueError:
        seen_award_id = 0
    new_awards = db.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.user_id == user.id,
               LedgerEntry.entry_type == EntryType.AWARD,
               LedgerEntry.id > seen_award_id)
        .order_by(LedgerEntry.id)
    ).all()
    latest_award_id = max((e.id for e in new_awards), default=seen_award_id)

    return _render(request, "dashboard.html", user, db,
                   recent=recent, pending=_pending_for_me(db, user),
                   my_open_requests=my_open_requests,
                   new_awards=new_awards, latest_award_id=latest_award_id,
                   **_daily_context(db, user), **_gifts_context(db, user), **extra)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User | None = Depends(auth.get_optional_user),
              db: Session = Depends(get_db)):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return _dashboard_render(request, db, user)


@router.post("/daily-answer", response_class=HTMLResponse)
def daily_answer_submit(request: Request, answer: str = Form(""),
                        user: User = Depends(auth.get_current_user),
                        db: Session = Depends(get_db)):
    error = None
    try:
        daily_service.answer_today(db, user, answer)
    except ValueError as e:
        error = str(e)
    return _dashboard_render(request, db, user, daily_error=error)


@router.post("/gifts/{gift_id}/open", response_class=HTMLResponse)
def gift_open(request: Request, gift_id: int,
              user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    opened = None
    try:
        opened = transactions.open_gift(db, user, gift_id)
    except (ValueError, PermissionError) as e:
        error = str(e)
    return _dashboard_render(request, db, user, gift_error=error, opened_gift=opened)


# ---------- award ----------

@router.get("/award", response_class=HTMLResponse)
def award_page(request: Request, user: User = Depends(auth.get_current_user),
               db: Session = Depends(get_db)):
    return _render(request, "award.html", user, db,
                   users=_users_except(db, user.id), categories=_categories(db))


@router.post("/award", response_class=HTMLResponse)
def award_submit(request: Request, to_user_id: int = Form(...), amount: int = Form(...),
                 reason: str = Form(""), category: str = Form(""),
                 seal: str = Form(""), unlock_date: str = Form(""),
                 user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    recipient = db.get(User, to_user_id)
    try:
        if seal:
            unlock_at = None
            if unlock_date.strip():
                from datetime import datetime, timedelta
                from app.services.daily import QUESTION_UTC_OFFSET
                # Date opens at local-ish midnight (same offset the daily
                # question uses), expressed in naive UTC.
                unlock_at = (datetime.strptime(unlock_date.strip(), "%Y-%m-%d")
                             - timedelta(hours=QUESTION_UTC_OFFSET))
            transactions.send_sealed_gift(db, user, to_user_id, amount, reason, unlock_at)
        else:
            transactions.award(db, user, to_user_id, amount, reason, category or None)
    except ValueError as e:
        return _render(request, "award.html", user, db,
                       users=_users_except(db, user.id), categories=_categories(db),
                       error=str(e), form={"to_user_id": to_user_id, "amount": amount,
                                           "reason": reason, "category": category})
    if seal:
        return _render(request, "award.html", user, db,
                       users=_users_except(db, user.id), categories=_categories(db),
                       sealed={"name": recipient.display_name,
                               "unlock_date": unlock_date.strip() or None})
    return _render(request, "award.html", user, db,
                   users=_users_except(db, user.id), categories=_categories(db),
                   awarded={"name": recipient.display_name, "amount": amount})


# ---------- spend / redemptions ----------

def _redemptions_context(db: Session, user: User) -> dict:
    mine = db.scalars(
        select(Redemption).where(Redemption.requester_id == user.id)
        .order_by(Redemption.created_at.desc())
    ).all()
    for_me = db.scalars(
        select(Redemption).where(Redemption.grantor_id == user.id)
        .order_by(Redemption.created_at.desc())
    ).all()
    for r in mine + for_me:
        if r.status == RedemptionStatus.APPROVED:
            r.waiting_days = transactions.waiting_days(r)
    return {"mine": mine, "for_me": for_me}


def _active_items(db: Session) -> list[Item]:
    return db.scalars(
        select(Item).where(Item.is_active == True)  # noqa: E712
        .order_by(Item.owner_id, Item.price)
    ).all()


def _spend_page(request: Request, db: Session, user: User, **extra):
    return _render(request, "spend.html", user, db,
                   users=_users_except(db, user.id), categories=_categories(db),
                   **_redemptions_context(db, user), **extra)


def _shop_page(request: Request, db: Session, user: User, **extra):
    menu_items = [i for i in _active_items(db) if i.owner_id != user.id]
    return _render(request, "shop.html", user, db, menu_items=menu_items, **extra)


def _settings_page(request: Request, db: Session, user: User, **extra):
    return _render(request, "settings.html", user, db, **extra)


def _offerings_page(request: Request, db: Session, user: User, **extra):
    my_items = [i for i in _active_items(db) if i.owner_id == user.id]
    return _render(request, "offerings.html", user, db,
                   my_items=my_items, categories=_categories(db), **extra)


@router.get("/spend", response_class=HTMLResponse)
def spend_page(request: Request, user: User = Depends(auth.get_current_user),
               db: Session = Depends(get_db)):
    return _spend_page(request, db, user)


@router.post("/spend", response_class=HTMLResponse)
def spend_submit(request: Request, grantor_id: int = Form(...), amount: int = Form(...),
                 reason: str = Form(""), category: str = Form(""),
                 user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    created = False
    try:
        transactions.create_redemption(db, user, grantor_id, amount, reason, category or None)
        created = True
    except ValueError as e:
        error = str(e)
    return _spend_page(request, db, user, error=error, created=created)


# ---------- shop (browse & redeem items) ----------

@router.get("/shop", response_class=HTMLResponse)
def shop_page(request: Request, user: User = Depends(auth.get_current_user),
              db: Session = Depends(get_db)):
    return _shop_page(request, db, user)


@router.post("/items/{item_id}/redeem", response_class=HTMLResponse)
def item_redeem(request: Request, item_id: int,
                user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    redeemed = None
    try:
        redemption = transactions.redeem_item(db, user, item_id)
        redeemed = redemption.reason
    except ValueError as e:
        error = str(e)
    return _shop_page(request, db, user, error=error, redeemed=redeemed)


# ---------- my offerings ----------

@router.get("/offerings", response_class=HTMLResponse)
def offerings_page(request: Request, user: User = Depends(auth.get_current_user),
                   db: Session = Depends(get_db)):
    return _offerings_page(request, db, user)


@router.post("/items", response_class=HTMLResponse)
def item_create(request: Request, name: str = Form(""), price: int = Form(...),
                category: str = Form(""),
                user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    listed = None
    try:
        item = transactions.create_item(db, user, name, price, category or None)
        listed = item.name
    except ValueError as e:
        error = str(e)
    return _offerings_page(request, db, user, items_error=error, listed=listed)


@router.post("/items/{item_id}/delist", response_class=HTMLResponse)
def item_delist(request: Request, item_id: int,
                user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    try:
        transactions.delist_item(db, user, item_id)
    except (ValueError, PermissionError) as e:
        error = str(e)
    return _offerings_page(request, db, user, items_error=error)


def _redemption_action(request: Request, db: Session, user: User, action,
                       redemption_id: int, **flags):
    error = None
    try:
        action(db, user, redemption_id)
    except (ValueError, PermissionError) as e:
        error = str(e)
        flags = {}
    return _render(request, "partials/redemption_lists.html", user, db,
                   error=error, **flags, **_redemptions_context(db, user))


@router.post("/redemptions/{redemption_id}/approve", response_class=HTMLResponse)
def approve(request: Request, redemption_id: int,
            user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _redemption_action(request, db, user, transactions.approve_redemption, redemption_id)


@router.post("/redemptions/{redemption_id}/deny", response_class=HTMLResponse)
def deny(request: Request, redemption_id: int,
         user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _redemption_action(request, db, user, transactions.deny_redemption, redemption_id)


@router.post("/redemptions/{redemption_id}/cancel", response_class=HTMLResponse)
def cancel(request: Request, redemption_id: int,
           user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _redemption_action(request, db, user, transactions.cancel_redemption, redemption_id)


@router.post("/redemptions/{redemption_id}/fulfill", response_class=HTMLResponse)
def fulfill(request: Request, redemption_id: int,
            user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _redemption_action(request, db, user, transactions.fulfill_redemption,
                              redemption_id, celebrate=True)


@router.post("/redemptions/{redemption_id}/nudge", response_class=HTMLResponse)
def nudge(request: Request, redemption_id: int,
          user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    nudged = False
    try:
        transactions.nudge_redemption(db, user, redemption_id)
        nudged = True
    except (ValueError, PermissionError) as e:
        error = str(e)
    return _render(request, "partials/redemption_lists.html", user, db,
                   error=error, nudged=nudged, **_redemptions_context(db, user))


# ---------- bets ----------

def _bets_page(request: Request, db: Session, user: User, **extra):
    bets = bets_service.bets_for(db, user.id)
    return _render(request, "bets.html", user, db,
                   users=_users_except(db, user.id),
                   proposed_to_me=[b for b in bets if b.status == BetStatus.PROPOSED and b.opponent_id == user.id],
                   proposed_by_me=[b for b in bets if b.status == BetStatus.PROPOSED and b.challenger_id == user.id],
                   active=[b for b in bets if b.status == BetStatus.ACTIVE],
                   history=[b for b in bets if b.status not in (BetStatus.PROPOSED, BetStatus.ACTIVE)][:20],
                   **extra)


@router.get("/bets", response_class=HTMLResponse)
def bets_page(request: Request, user: User = Depends(auth.get_current_user),
              db: Session = Depends(get_db)):
    return _bets_page(request, db, user)


@router.post("/bets", response_class=HTMLResponse)
def bet_propose(request: Request, opponent_id: int = Form(...), stake: int = Form(...),
                terms: str = Form(""),
                user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    proposed = False
    try:
        bets_service.propose_bet(db, user, opponent_id, stake, terms)
        proposed = True
    except ValueError as e:
        error = str(e)
    return _bets_page(request, db, user, error=error, proposed=proposed)


def _bet_action(request: Request, db: Session, user: User, fn, bet_id: int, **flags):
    error = None
    try:
        fn(db, user, bet_id)
    except (ValueError, PermissionError) as e:
        error = str(e)
        flags = {}
    return _bets_page(request, db, user, error=error, **flags)


@router.post("/bets/{bet_id}/accept", response_class=HTMLResponse)
def bet_accept(request: Request, bet_id: int,
               user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _bet_action(request, db, user, bets_service.accept_bet, bet_id, accepted=True)


@router.post("/bets/{bet_id}/decline", response_class=HTMLResponse)
def bet_decline(request: Request, bet_id: int,
                user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _bet_action(request, db, user, bets_service.decline_bet, bet_id)


@router.post("/bets/{bet_id}/cancel", response_class=HTMLResponse)
def bet_cancel(request: Request, bet_id: int,
               user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _bet_action(request, db, user, bets_service.cancel_bet, bet_id)


@router.post("/bets/{bet_id}/concede", response_class=HTMLResponse)
def bet_concede(request: Request, bet_id: int,
                user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _bet_action(request, db, user, bets_service.concede_bet, bet_id, conceded=True)


@router.post("/bets/{bet_id}/void", response_class=HTMLResponse)
def bet_void(request: Request, bet_id: int,
             user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _bet_action(request, db, user, bets_service.void_bet, bet_id)


# ---------- memories ----------

@router.get("/memories", response_class=HTMLResponse)
def memories_page(request: Request, user: User = Depends(auth.get_current_user),
                  db: Session = Depends(get_db)):
    return _render(request, "memories.html", user, db,
                   items=memories_service.timeline(db))


@router.post("/memories/note", response_class=HTMLResponse)
def memories_note(request: Request, kind: str = Form(""), ref_id: int = Form(...),
                  note: str = Form(""),
                  user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    try:
        memories_service.upsert_note(db, user, kind, ref_id, note)
    except ValueError as e:
        error = str(e)
    return _render(request, "memories.html", user, db,
                   items=memories_service.timeline(db), error=error)


# ---------- notifications ----------

@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, user: User = Depends(auth.get_current_user),
                       db: Session = Depends(get_db)):
    rows = notify_service.list_and_mark_read(db, user.id)
    return _render(request, "notifications.html", user, db, notifications=rows)


# ---------- settings ----------

@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: User = Depends(auth.get_current_user),
                  db: Session = Depends(get_db)):
    return _settings_page(request, db, user)


@router.post("/settings/account", response_class=HTMLResponse)
def settings_account(request: Request, email: str = Form(""), display_name: str = Form(""),
                     current_password: str = Form(""),
                     user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    saved = False
    try:
        auth.update_account(db, user, email, display_name, current_password)
        saved = True
    except ValueError as e:
        error = str(e)
    return _settings_page(request, db, user, account_error=error, account_saved=saved)


@router.post("/settings/avatar", response_class=HTMLResponse)
def settings_avatar(request: Request, avatar: str = Form(""),
                    user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    saved = False
    try:
        auth.update_avatar(db, user, avatar)
        saved = True
    except ValueError as e:
        error = str(e)
    return _settings_page(request, db, user, avatar_error=error, avatar_saved=saved)


@router.post("/settings/password", response_class=HTMLResponse)
def settings_password(request: Request, current_password: str = Form(""),
                      new_password: str = Form(""),
                      user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    error = None
    saved = False
    try:
        auth.change_password(db, user, current_password, new_password)
        saved = True
    except ValueError as e:
        error = str(e)
    return _settings_page(request, db, user, password_error=error, password_saved=saved)


# ---------- ledger ----------

def _ledger_query(db: Session, user_id: int, entry_type: str | None,
                  category: str | None, page: int):
    q = select(LedgerEntry).where(LedgerEntry.user_id == user_id)
    if entry_type:
        q = q.where(LedgerEntry.entry_type == entry_type)
    if category:
        q = q.where(LedgerEntry.category == category)
    q = q.order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc())
    entries = db.scalars(q.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE + 1)).all()
    has_next = len(entries) > PAGE_SIZE
    return entries[:PAGE_SIZE], has_next


@router.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request, page: int = 1, entry_type: str = "", category: str = "",
                user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    page = max(page, 1)
    entries, has_next = _ledger_query(db, user.id, entry_type or None, category or None, page)
    ctx = dict(entries=entries, page=page, has_next=has_next,
               entry_type=entry_type, category=category, categories=_categories(db))
    template = "partials/ledger_table.html" if request.headers.get("hx-request") else "ledger.html"
    return _render(request, template, user, db, **ctx)


# ---------- reports ----------

@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, user: User = Depends(auth.get_current_user),
                 db: Session = Depends(get_db)):
    return _render(request, "reports.html", user, db)


# ---------- admin ----------

def _admin_page(request: Request, db: Session, user: User,
                view_user_id: int | None = None, **extra):
    users = db.scalars(select(User).order_by(User.display_name)).all()
    rows = [{"user": u, "balance": get_balance(db, u.id)} for u in users]
    viewed = db.get(User, view_user_id) if view_user_id else None
    viewed_entries = []
    if viewed:
        viewed_entries = db.scalars(
            select(LedgerEntry).where(LedgerEntry.user_id == viewed.id)
            .order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc()).limit(100)
        ).all()
    category_rows = db.scalars(select(Category).order_by(Category.id)).all()
    return _render(request, "admin.html", user, db, rows=rows,
                   viewed=viewed, viewed_entries=viewed_entries,
                   category_rows=category_rows, invite_code=get_invite_code(db),
                   **extra)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, view_user_id: int | None = None,
               user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    if not user.is_admin:
        return RedirectResponse("/", status_code=303)
    return _admin_page(request, db, user, view_user_id)


def _admin_action(request: Request, db: Session, user: User, fn, *args,
                  success: str):
    """Run an admin service call and re-render the admin page with the result."""
    error = None
    done = None
    try:
        fn(db, user, *args)
        done = success
    except (ValueError, PermissionError) as e:
        error = str(e)
    return _admin_page(request, db, user, error=error, done=done)


@router.post("/admin/adjustments", response_class=HTMLResponse)
def admin_adjustment(request: Request, user_id: int = Form(...), amount: int = Form(...),
                     reason: str = Form(""),
                     user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _admin_action(request, db, user, transactions.adjustment, user_id, amount, reason,
                         success="Adjustment recorded in the ledger. ⚖️")


@router.post("/admin/users", response_class=HTMLResponse)
def admin_create_user(request: Request, email: str = Form(""), display_name: str = Form(""),
                      password: str = Form(""),
                      user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _admin_action(request, db, user, admin_service.create_user,
                         email, display_name, password,
                         success=f"{display_name.strip() or 'User'} has joined the economy. 🎉")


@router.post("/admin/users/{user_id}/password", response_class=HTMLResponse)
def admin_set_password(request: Request, user_id: int, new_password: str = Form(""),
                       user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _admin_action(request, db, user, admin_service.set_user_password,
                         user_id, new_password, success="Password reset. 🔒")


@router.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
def admin_delete_user(request: Request, user_id: int,
                      user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _admin_action(request, db, user, admin_service.delete_user, user_id,
                         success="User deleted. Like they were never here. 🫥")


@router.post("/admin/invite-code", response_class=HTMLResponse)
def admin_invite_code(request: Request, code: str = Form(""),
                      user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _admin_action(request, db, user, admin_service.update_invite_code, code,
                         success="Invite code updated. Spread the word (selectively). 🤫")


@router.post("/admin/categories", response_class=HTMLResponse)
def admin_add_category(request: Request, name: str = Form(""),
                       user: User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    return _admin_action(request, db, user, admin_service.add_category, name,
                         success="Category added. 🏷️")


@router.post("/admin/categories/{category_id}/delete", response_class=HTMLResponse)
def admin_remove_category(request: Request, category_id: int,
                          user: User = Depends(auth.get_current_user),
                          db: Session = Depends(get_db)):
    return _admin_action(request, db, user, admin_service.remove_category, category_id,
                         success="Category removed. History keeps its receipts. 📜")
