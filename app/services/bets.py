"""Bets: two users stake points on an outcome; winner takes the pot.

Ledger convention (extends the redemption hold convention documented in
services/ledger.py):
- Propose: HOLD (−stake, bet_id) for the challenger.
- Accept:  HOLD (−stake, bet_id) for the opponent; bet becomes ACTIVE.
- A bet HOLD reduces spendable balance while its bet is PROPOSED or ACTIVE.
- Decline/Cancel/Void: RELEASE (+stake) per held party; holds stop counting.
- Settle (via concede): loser gets a DEBIT (−stake, permanent spend);
  winner gets a RELEASE (+stake, own hold back) plus an AWARD (+stake,
  the winnings, counterparty = loser). Winnings being a real AWARD means
  they show up in reports and trigger the dashboard celebration.
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Bet, BetStatus, EntryType, LedgerEntry, User, utcnow
from app.services.ledger import get_balance
from app.services.notify import notify


def _get_bet(db: Session, bet_id: int) -> Bet:
    bet = db.get(Bet, bet_id)
    if bet is None:
        raise ValueError("That bet doesn't exist.")
    return bet


def _hold(db: Session, bet: Bet, user_id: int, counterparty_id: int) -> None:
    db.add(LedgerEntry(
        user_id=user_id,
        counterparty_id=counterparty_id,
        entry_type=EntryType.HOLD,
        amount=-bet.stake,
        reason=f"Bet: {bet.terms}",
        category="bet",
        bet_id=bet.id,
        created_by=user_id,
    ))


def _release(db: Session, bet: Bet, user_id: int, counterparty_id: int,
             created_by: int) -> None:
    db.add(LedgerEntry(
        user_id=user_id,
        counterparty_id=counterparty_id,
        entry_type=EntryType.RELEASE,
        amount=bet.stake,
        reason=f"Bet: {bet.terms}",
        category="bet",
        bet_id=bet.id,
        created_by=created_by,
    ))


def propose_bet(db: Session, challenger: User, opponent_id: int, stake: int,
                terms: str) -> Bet:
    if stake <= 0:
        raise ValueError("Stake must be a positive number of Brownie Points.")
    if opponent_id == challenger.id:
        raise ValueError("Betting against yourself is called indecision.")
    if db.get(User, opponent_id) is None:
        raise ValueError("That user doesn't exist.")
    if not terms.strip():
        raise ValueError("What's the bet? Write the terms.")
    if get_balance(db, challenger.id).spendable < stake:
        raise ValueError(
            f"Not enough points to stake {stake} BP — you have "
            f"{get_balance(db, challenger.id).spendable} spendable."
        )

    bet = Bet(challenger_id=challenger.id, opponent_id=opponent_id,
              stake=stake, terms=terms.strip())
    db.add(bet)
    db.flush()
    _hold(db, bet, challenger.id, opponent_id)
    notify(db, opponent_id,
           f"🎲 {challenger.display_name} challenges you: “{bet.terms}” — {stake} BP each. "
           f"Accept?", "/bets")
    db.commit()
    return bet


def accept_bet(db: Session, actor: User, bet_id: int) -> Bet:
    bet = _get_bet(db, bet_id)
    if actor.id != bet.opponent_id:
        raise PermissionError("Only the person challenged can accept this bet.")
    if bet.status != BetStatus.PROPOSED:
        raise ValueError("This bet isn't open for accepting.")
    if get_balance(db, actor.id).spendable < bet.stake:
        raise ValueError(
            f"You can't cover the stake: it's {bet.stake} BP and you have "
            f"{get_balance(db, actor.id).spendable} spendable."
        )
    bet.status = BetStatus.ACTIVE
    _hold(db, bet, actor.id, bet.challenger_id)
    notify(db, bet.challenger_id,
           f"🤝 {actor.display_name} accepted your bet: “{bet.terms}”. "
           f"{bet.stake * 2} BP on the line!", "/bets")
    db.commit()
    return bet


def decline_bet(db: Session, actor: User, bet_id: int) -> Bet:
    bet = _get_bet(db, bet_id)
    if actor.id != bet.opponent_id:
        raise PermissionError("Only the person challenged can decline this bet.")
    if bet.status != BetStatus.PROPOSED:
        raise ValueError("This bet isn't open for declining.")
    bet.status = BetStatus.DECLINED
    bet.resolved_at = utcnow()
    _release(db, bet, bet.challenger_id, actor.id, actor.id)
    notify(db, bet.challenger_id,
           f"🙅 {actor.display_name} declined your bet: “{bet.terms}”. Stake returned.",
           "/bets")
    db.commit()
    return bet


def cancel_bet(db: Session, actor: User, bet_id: int) -> Bet:
    bet = _get_bet(db, bet_id)
    if actor.id != bet.challenger_id:
        raise PermissionError("Only the challenger can withdraw a proposed bet.")
    if bet.status != BetStatus.PROPOSED:
        raise ValueError("Only proposed bets can be withdrawn.")
    bet.status = BetStatus.CANCELLED
    bet.resolved_at = utcnow()
    _release(db, bet, bet.challenger_id, bet.opponent_id, actor.id)
    notify(db, bet.opponent_id,
           f"🫥 {actor.display_name} withdrew the bet: “{bet.terms}”.", "/bets")
    db.commit()
    return bet


def concede_bet(db: Session, actor: User, bet_id: int) -> Bet:
    """The actor admits defeat; the other party wins the pot."""
    bet = _get_bet(db, bet_id)
    if actor.id not in (bet.challenger_id, bet.opponent_id):
        raise PermissionError("You're not part of this bet.")
    if bet.status != BetStatus.ACTIVE:
        raise ValueError("Only active bets can be settled.")

    winner_id = bet.opponent_id if actor.id == bet.challenger_id else bet.challenger_id
    bet.status = BetStatus.SETTLED
    bet.winner_id = winner_id
    bet.resolved_at = utcnow()

    # Loser's hold becomes a permanent debit.
    db.add(LedgerEntry(
        user_id=actor.id, counterparty_id=winner_id, entry_type=EntryType.DEBIT,
        amount=-bet.stake, reason=f"Lost bet: {bet.terms}", category="bet",
        bet_id=bet.id, created_by=actor.id,
    ))
    # Winner gets their own stake back plus the winnings.
    _release(db, bet, winner_id, actor.id, actor.id)
    db.add(LedgerEntry(
        user_id=winner_id, counterparty_id=actor.id, entry_type=EntryType.AWARD,
        amount=bet.stake, reason=f"Won bet: {bet.terms}", category="bet",
        bet_id=bet.id, created_by=actor.id,
    ))
    notify(db, winner_id,
           f"🏆 {actor.display_name} conceded! You win “{bet.terms}” — +{bet.stake} BP.",
           "/bets")
    db.commit()
    return bet


def void_bet(db: Session, admin: User, bet_id: int) -> Bet:
    """Admin escape hatch for disputed bets: everyone gets their stake back."""
    if not admin.is_admin:
        raise PermissionError("Only admins can void bets.")
    bet = _get_bet(db, bet_id)
    if bet.status not in (BetStatus.PROPOSED, BetStatus.ACTIVE):
        raise ValueError("Only proposed or active bets can be voided.")
    was_active = bet.status == BetStatus.ACTIVE
    bet.status = BetStatus.VOIDED
    bet.resolved_at = utcnow()
    _release(db, bet, bet.challenger_id, bet.opponent_id, admin.id)
    if was_active:
        _release(db, bet, bet.opponent_id, bet.challenger_id, admin.id)
    for uid in (bet.challenger_id, bet.opponent_id):
        notify(db, uid, f"⚖️ An admin voided the bet “{bet.terms}”. Stakes returned.",
               "/bets")
    db.commit()
    return bet


def bets_for(db: Session, user_id: int) -> list[Bet]:
    return db.scalars(
        select(Bet)
        .where(or_(Bet.challenger_id == user_id, Bet.opponent_id == user_id))
        .order_by(Bet.created_at.desc(), Bet.id.desc())
    ).all()
