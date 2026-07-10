"""Admin-only management operations: users, categories, invite code.

Same conventions as the transactions service: ValueError for validation
failures with user-facing messages, PermissionError when the actor
isn't allowed.
"""

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app import auth
from app.models import Category, Item, LedgerEntry, Redemption, User
from app.services import settings as settings_service


def _require_admin(user: User) -> None:
    if not user.is_admin:
        raise PermissionError("Only admins can do that.")


# ---------- users ----------

def create_user(db: Session, admin: User, email: str, display_name: str,
                password: str) -> User:
    _require_admin(admin)
    email = auth._validate_email(db, email)
    display_name = display_name.strip()
    if not display_name:
        raise ValueError("Pick a display name for them.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    user = User(email=email, display_name=display_name,
                password_hash=auth.hash_password(password), is_admin=False)
    db.add(user)
    db.commit()
    return user


def set_user_password(db: Session, admin: User, user_id: int,
                      new_password: str) -> User:
    _require_admin(admin)
    target = db.get(User, user_id)
    if target is None:
        raise ValueError("That user doesn't exist.")
    if len(new_password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    target.password_hash = auth.hash_password(new_password)
    db.commit()
    return target


def delete_user(db: Session, admin: User, target_user_id: int) -> None:
    """Hard-delete a user who has never actually transacted.

    Refuses if the user appears anywhere in the ledger (as owner,
    counterparty, or creator of an entry), in redemptions, or owns
    items — deleting those would either orphan foreign keys or erase
    entries that belong to someone else's history. For a user with real
    activity, reset their password instead to lock them out.
    """
    _require_admin(admin)
    target = db.get(User, target_user_id)
    if target is None:
        raise ValueError("That user doesn't exist.")
    if target.id == admin.id:
        raise ValueError("You can't delete your own account this way.")

    has_ledger_activity = db.scalar(
        select(exists().where(or_(
            LedgerEntry.user_id == target.id,
            LedgerEntry.counterparty_id == target.id,
            LedgerEntry.created_by == target.id,
        )))
    )
    has_redemptions = db.scalar(
        select(exists().where(or_(
            Redemption.requester_id == target.id,
            Redemption.grantor_id == target.id,
        )))
    )
    has_items = db.scalar(select(exists().where(Item.owner_id == target.id)))

    if has_ledger_activity or has_redemptions or has_items:
        raise ValueError(
            f"{target.display_name} has ledger history — deleting them would "
            f"corrupt other people's records. Reset their password to lock "
            f"them out instead."
        )

    db.delete(target)
    db.commit()


# ---------- invite code ----------

def update_invite_code(db: Session, admin: User, code: str) -> str:
    _require_admin(admin)
    code = code.strip()
    if not code:
        raise ValueError("The invite code can't be empty.")
    settings_service.set_invite_code(db, code)
    return code


# ---------- categories ----------

def add_category(db: Session, admin: User, name: str) -> Category:
    _require_admin(admin)
    name = name.strip().lower()
    if not name:
        raise ValueError("Category name can't be empty.")
    if db.scalar(select(Category).where(Category.name == name)) is not None:
        raise ValueError(f"“{name}” is already a category.")
    category = Category(name=name)
    db.add(category)
    db.commit()
    return category


def remove_category(db: Session, admin: User, category_id: int) -> None:
    """Remove a category from the picker. Historical ledger entries keep
    the category as plain text, so nothing breaks."""
    _require_admin(admin)
    category = db.get(Category, category_id)
    if category is None:
        raise ValueError("That category doesn't exist.")
    db.delete(category)
    db.commit()
