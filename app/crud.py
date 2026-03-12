from __future__ import annotations

import datetime as dt
import json
import secrets
from typing import Optional

from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from .models import User, Key, KeyStatus, Transaction, TxType


def rub_to_cents(rub: int) -> int:
    return int(rub) * 100


def cents_to_rub_str(cents: int) -> str:
    rub = cents / 100.0
    # show without trailing zeros if possible
    s = f"{rub:.2f}"
    if s.endswith("00"):
        s = s[:-3]
    elif s.endswith("0"):
        s = s[:-1]
    return s


def get_or_create_user(session: Session, tg_id: int, username: str | None, first_name: str | None, last_name: str | None) -> User:
    user = session.scalar(select(User).where(User.tg_id == tg_id))
    now = dt.datetime.utcnow()
    if user:
        # update basic fields
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.last_activity_at = now
        return user

    user = User(tg_id=tg_id, username=username, first_name=first_name, last_name=last_name, last_activity_at=now)
    session.add(user)
    session.flush()
    return user


def find_user_by_referral_code(session: Session, code: str) -> User | None:
    return session.scalar(select(User).where(User.referral_code == code))


def apply_referral_bonus(session: Session, new_user: User, referrer: User, bonus_cents: int) -> bool:
    if new_user.referrer_id is not None:
        return False
    if new_user.id == referrer.id:
        return False
    new_user.referrer_id = referrer.id
    referrer.balance_cents += bonus_cents
    session.add(Transaction(user_id=referrer.id, type=TxType.referral_bonus, amount_cents=bonus_cents,
                            meta_json=json.dumps({"from_user_id": new_user.id, "from_tg_id": new_user.tg_id})))
    return True


def get_user_stats(session: Session, user_id: int) -> dict:
    active_keys = session.scalar(select(func.count(Key.id)).where(Key.owner_id == user_id, Key.status == KeyStatus.active)) or 0
    paused_keys = session.scalar(select(func.count(Key.id)).where(Key.owner_id == user_id, Key.status == KeyStatus.paused)) or 0
    total_keys = active_keys + paused_keys
    return {"active_keys": active_keys, "paused_keys": paused_keys, "total_keys": total_keys}


def list_user_keys(session: Session, user_id: int) -> list[Key]:
    return list(
        session.scalars(
            select(Key)
            .where(Key.owner_id == user_id, Key.status.in_([KeyStatus.active, KeyStatus.paused]))
            .order_by(Key.id.desc())
        )
    )


def pick_free_key(session: Session) -> Key | None:
    return session.scalar(select(Key).where(Key.status == KeyStatus.free).order_by(Key.id.asc()).limit(1))


def assign_key_to_user(session: Session, key: Key, user: User, activate: bool) -> Key:
    key.owner_id = user.id
    key.assigned_at = dt.datetime.utcnow()
    key.status = KeyStatus.active if activate else KeyStatus.paused
    session.add(key)
    return key




def release_key_to_pool(session: Session, key: Key) -> Key:
    key.owner_id = None
    key.assigned_at = None
    key.last_billed_date = None
    key.status = KeyStatus.free
    session.add(key)
    return key

def set_key_status(session: Session, key_id: int, user_id: int, status: KeyStatus) -> bool:
    key = session.scalar(select(Key).where(Key.id == key_id, Key.owner_id == user_id))
    if not key:
        return False
    key.status = status
    session.add(key)
    return True


def add_topup(session: Session, user: User, amount_cents: int, meta: dict | None = None, tx_type: TxType = TxType.topup):
    user.balance_cents += amount_cents
    user.last_activity_at = dt.datetime.utcnow()
    if tx_type in (TxType.topup, TxType.admin_adjust) and amount_cents > 0:
        user.last_paid_at = dt.datetime.utcnow()
    session.add(Transaction(user_id=user.id, type=tx_type, amount_cents=amount_cents, meta_json=json.dumps(meta or {})))


def debit(session: Session, user: User, amount_cents: int, meta: dict | None = None):
    # amount_cents should be positive, will be stored negative
    user.balance_cents -= amount_cents
    session.add(Transaction(user_id=user.id, type=TxType.debit, amount_cents=-amount_cents, meta_json=json.dumps(meta or {})))


def bill_daily(
    session: Session,
    price_per_key_per_day_cents: int,
    today: dt.date | None = None,
    insufficient_action: str = "pause",
) -> dict:
    """
    Bills users once per day for active keys.
    Returns stats for logging.
    """
    if today is None:
        today = dt.date.today()

    billed_users = 0
    paused_users = 0
    revoked_users = 0
    total_debit = 0

    # actions to perform outside the DB transaction (e.g. 3x-ui calls)
    to_revoke: list[dict] = []
    to_pause: list[dict] = []

    users = list(session.scalars(select(User).where(User.is_banned == False)))  # noqa: E712
    for user in users:
        active_keys = list(session.scalars(select(Key).where(Key.owner_id == user.id, Key.status == KeyStatus.active)))
        if not active_keys:
            continue

        # Do not double bill if already billed today (for all keys)
        # If any key has last_billed_date == today, assume billed.
        if any(k.last_billed_date == today for k in active_keys):
            continue

        cost = price_per_key_per_day_cents * len(active_keys)
        if user.balance_cents >= cost:
            debit(session, user, cost, meta={"keys": len(active_keys), "date": str(today)})
            total_debit += cost
            billed_users += 1
            for k in active_keys:
                k.last_billed_date = today
                session.add(k)
        else:
            # not enough: pause or revoke keys, debit remaining balance to zero
            if user.balance_cents > 0:
                debit(session, user, user.balance_cents, meta={"keys": len(active_keys), "date": str(today), "reason": "insufficient"})
                total_debit += user.balance_cents
            user.balance_cents = 0
            act = (insufficient_action or "pause").lower()
            if act not in ("pause", "revoke"):
                act = "pause"

            for k in active_keys:
                if act == "revoke":
                    k.status = KeyStatus.revoked
                    to_revoke.append({
                        "key_id": k.id,
                        "xui_inbound_id": k.xui_inbound_id,
                        "xui_client_id": k.xui_client_id,
                    })
                else:
                    k.status = KeyStatus.paused
                    to_pause.append({
                        "key_id": k.id,
                        "xui_inbound_id": k.xui_inbound_id,
                        "xui_client_id": k.xui_client_id,
                        "xui_client_json": k.xui_client_json,
                    })
                session.add(k)

            if act == "revoke":
                revoked_users += 1
            else:
                paused_users += 1

    return {
        "billed_users": billed_users,
        "paused_users": paused_users,
        "revoked_users": revoked_users,
        "total_debit_cents": total_debit,
        "actions": {"revoke": to_revoke, "pause": to_pause},
    }


def admin_find_user(session: Session, q: str) -> list[User]:
    q = q.strip()
    if not q:
        return []
    users = []
    if q.isdigit():
        tg_id = int(q)
        users = list(session.scalars(select(User).where(User.tg_id == tg_id).limit(50)))
    if not users:
        users = list(session.scalars(select(User).where(User.username.ilike(f"%{q}%")).limit(50)))
    return users
