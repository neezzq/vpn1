from __future__ import annotations

import datetime as dt
import enum
import secrets
import string

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class KeyStatus(str, enum.Enum):
    free = "free"
    active = "active"
    paused = "paused"
    revoked = "revoked"


def _new_ref_code(n: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), default=dt.datetime.utcnow)
    last_activity_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), default=dt.datetime.utcnow)
    last_paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)

    balance_cents: Mapped[int] = mapped_column(Integer, default=0)

    referral_code: Mapped[str] = mapped_column(String(24), unique=True, index=True, default=_new_ref_code)
    referrer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    current_key_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_key_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    referrer = relationship("User", remote_side=[id], backref="referrals")


class Key(Base):
    __tablename__ = "keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(16), default="vless")

    # raw config URI like vless://... OR a subscription URL
    config_uri: Mapped[str] = mapped_column(Text)

    # token for share link: /k/{token}
    share_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=lambda: secrets.token_urlsafe(16))

    status: Mapped[KeyStatus] = mapped_column(Enum(KeyStatus), default=KeyStatus.free)

    owner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    owner = relationship("User", backref="keys")

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), default=dt.datetime.utcnow)
    assigned_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_billed_date: Mapped[dt.date | None] = mapped_column(nullable=True)
    last_config_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)

    # Optional: 3X-UI integration fields
    xui_inbound_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    xui_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # UUID
    xui_email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    xui_client_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # stores full client object for updateClient




class PaymentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    expired = "expired"
    failed = "failed"


class PaymentProvider(str, enum.Enum):
    cryptobot = "cryptobot"
    platega = "platega"
    telegram = "telegram"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    user = relationship("User", backref="payments")

    provider: Mapped[PaymentProvider] = mapped_column(Enum(PaymentProvider), default=PaymentProvider.cryptobot)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending, index=True)
    amount_rub: Mapped[int] = mapped_column(Integer)
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(16), default="RUB")
    external_invoice_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pay_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), default=dt.datetime.utcnow)
    paid_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)

class TxType(str, enum.Enum):
    topup = "topup"
    debit = "debit"
    referral_bonus = "referral_bonus"
    admin_adjust = "admin_adjust"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    user = relationship("User", backref="transactions")

    type: Mapped[TxType] = mapped_column(Enum(TxType))
    amount_cents: Mapped[int] = mapped_column(Integer)  # positive for topup/bonus, negative for debit

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), default=dt.datetime.utcnow)

    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)


Index("ix_keys_owner_status", Key.owner_id, Key.status)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=False), default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)
