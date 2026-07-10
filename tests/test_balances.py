"""Unit tests for the derived-balance math (spec §5–6)."""

import pytest

from app.auth import register_user
from app.services import transactions
from app.services.ledger import get_balance


@pytest.fixture
def users(db):
    alice = register_user(db, "alice@example.com", "Alice", "password1", "test-invite")
    bob = register_user(db, "bob@example.com", "Bob", "password1", "test-invite")
    return alice, bob


def test_award_increases_recipient_balance(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 5, "airport run", "favor")

    assert get_balance(db, bob.id).spendable == 5
    assert get_balance(db, bob.id).lifetime_earned == 5
    assert get_balance(db, alice.id).spendable == 0  # giving costs nothing


def test_award_validations(db, users):
    alice, bob = users
    with pytest.raises(ValueError):
        transactions.award(db, alice, bob.id, 0, "zero", None)
    with pytest.raises(ValueError):
        transactions.award(db, alice, bob.id, -3, "negative", None)
    with pytest.raises(ValueError):
        transactions.award(db, alice, alice.id, 1, "self-award", None)
    with pytest.raises(ValueError):
        transactions.award(db, alice, bob.id, 1, "   ", None)


def test_hold_escrows_points_immediately(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 10, "reasons", None)
    transactions.create_redemption(db, bob, alice.id, 4, "make me dinner", "favor")

    balance = get_balance(db, bob.id)
    assert balance.spendable == 6
    assert balance.held == 4
    assert balance.spent == 0
    assert balance.lifetime_earned == 10


def test_cannot_double_spend_held_points(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 5, "reasons", None)
    transactions.create_redemption(db, bob, alice.id, 4, "favor one", None)
    with pytest.raises(ValueError, match="Not enough points"):
        transactions.create_redemption(db, bob, alice.id, 4, "favor two", None)


def test_insufficient_balance_rejected(db, users):
    alice, bob = users
    with pytest.raises(ValueError, match="Not enough points"):
        transactions.create_redemption(db, bob, alice.id, 1, "anything", None)


def test_approve_keeps_hold_fulfill_converts_to_debit(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 10, "reasons", None)
    r = transactions.create_redemption(db, bob, alice.id, 4, "dinner", None)

    transactions.approve_redemption(db, alice, r.id)
    balance = get_balance(db, bob.id)
    assert balance.spendable == 6  # still held while APPROVED
    assert balance.held == 4

    transactions.fulfill_redemption(db, alice, r.id)
    balance = get_balance(db, bob.id)
    assert balance.spendable == 6  # same number, but now a permanent debit
    assert balance.held == 0
    assert balance.spent == 4


def test_deny_releases_hold(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 10, "reasons", None)
    r = transactions.create_redemption(db, bob, alice.id, 4, "dinner", None)
    transactions.deny_redemption(db, alice, r.id)

    balance = get_balance(db, bob.id)
    assert balance.spendable == 10
    assert balance.held == 0
    assert balance.spent == 0


def test_cancel_releases_hold(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 10, "reasons", None)
    r = transactions.create_redemption(db, bob, alice.id, 4, "dinner", None)
    transactions.cancel_redemption(db, bob, r.id)

    assert get_balance(db, bob.id).spendable == 10


def test_lifecycle_permissions(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 10, "reasons", None)
    r = transactions.create_redemption(db, bob, alice.id, 4, "dinner", None)

    with pytest.raises(PermissionError):
        transactions.approve_redemption(db, bob, r.id)  # requester can't approve
    with pytest.raises(PermissionError):
        transactions.deny_redemption(db, bob, r.id)  # requester can't deny
    with pytest.raises(ValueError):
        transactions.fulfill_redemption(db, alice, r.id)  # not yet approved

    transactions.approve_redemption(db, alice, r.id)
    with pytest.raises(ValueError):
        transactions.cancel_redemption(db, bob, r.id)  # can only cancel while PENDING


def test_adjustments(db, users):
    alice, bob = users  # alice registered first → admin
    assert alice.is_admin and not bob.is_admin

    transactions.adjustment(db, alice, bob.id, 3, "goodwill grant")
    assert get_balance(db, bob.id).spendable == 3

    transactions.adjustment(db, alice, bob.id, -2, "correction")
    assert get_balance(db, bob.id).spendable == 1

    with pytest.raises(PermissionError):
        transactions.adjustment(db, bob, alice.id, 5, "sneaky")
    with pytest.raises(ValueError):
        transactions.adjustment(db, alice, bob.id, 0, "pointless")


def test_negative_adjustment_does_not_inflate_lifetime(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 10, "reasons", None)
    transactions.adjustment(db, alice, bob.id, -3, "correction")
    balance = get_balance(db, bob.id)
    assert balance.spendable == 7
    assert balance.lifetime_earned == 10  # lifetime = awards + max(adjustments, 0)


def test_item_redeem_creates_approved_redemption_with_hold(db, users):
    alice, bob = users
    transactions.award(db, alice, bob.id, 10, "reasons", None)
    item = transactions.create_item(db, alice, "back massage", 3, "favor")

    r = transactions.redeem_item(db, bob, item.id)
    assert r.status == "APPROVED"
    assert r.grantor_id == alice.id
    assert r.amount == 3
    assert r.reason == "back massage"

    balance = get_balance(db, bob.id)
    assert balance.spendable == 7
    assert balance.held == 3

    # Fulfillment converts the hold to a permanent debit as usual.
    transactions.fulfill_redemption(db, alice, r.id)
    balance = get_balance(db, bob.id)
    assert balance.spendable == 7
    assert balance.held == 0
    assert balance.spent == 3


def test_item_validations(db, users):
    alice, bob = users
    with pytest.raises(ValueError):
        transactions.create_item(db, alice, "freebie", 0, None)
    with pytest.raises(ValueError):
        transactions.create_item(db, alice, "   ", 2, None)

    item = transactions.create_item(db, alice, "car wash", 2, "chore")

    # Owner can't redeem their own item.
    with pytest.raises(ValueError):
        transactions.redeem_item(db, alice, item.id)
    # Bob has no points.
    with pytest.raises(ValueError, match="Not enough points"):
        transactions.redeem_item(db, bob, item.id)

    # Only the owner (or admin) can delist.
    with pytest.raises(PermissionError):
        transactions.delist_item(db, bob, item.id)
    transactions.delist_item(db, alice, item.id)

    # Delisted items can't be redeemed.
    transactions.award(db, alice, bob.id, 10, "reasons", None)
    with pytest.raises(ValueError, match="no longer on the menu"):
        transactions.redeem_item(db, bob, item.id)


def test_balance_always_equals_ledger_sum_convention(db, users):
    """Spec §12: no drift — walk a busy history and reconcile."""
    alice, bob = users
    transactions.award(db, alice, bob.id, 10, "a", None)
    transactions.award(db, bob, alice.id, 3, "b", None)
    r1 = transactions.create_redemption(db, bob, alice.id, 4, "r1", None)
    r2 = transactions.create_redemption(db, bob, alice.id, 2, "r2", None)
    transactions.approve_redemption(db, alice, r1.id)
    transactions.fulfill_redemption(db, alice, r1.id)
    transactions.deny_redemption(db, alice, r2.id)
    transactions.adjustment(db, alice, bob.id, -1, "adj")

    balance = get_balance(db, bob.id)
    # 10 awarded − 4 fulfilled − 1 adjustment, r2's hold released
    assert balance.spendable == 5
    assert balance.held == 0
    assert balance.spent == 4
    assert get_balance(db, alice.id).spendable == 3
