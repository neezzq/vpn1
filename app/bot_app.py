from __future__ import annotations

import asyncio
import datetime as dt
import json
import secrets
from typing import Iterable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from .config import Settings
from .crud import add_topup, cents_to_rub_str, get_or_create_user
from .db import session_scope
from .hooks import run_hook
from .models import Key, KeyStatus, Transaction, TxType, User, Payment, PaymentStatus, PaymentProvider
from .three_xui import ThreeXUI, ThreeXUIConfig
from .ui_content import get_bot_ui, format_ui_text, get_button_config
from .cryptobot import CryptoBotClient
from .platega import PlategaClient, PlategaError
from .vpn import apply_template, gen_uuid


class RenameDeviceState(StatesGroup):
    waiting_for_name = State()


class CustomAmountState(StatesGroup):
    waiting_for_amount = State()


class DeviceActionCb(CallbackData, prefix="dev"):
    action: str
    key_id: int


class PlatformCb(CallbackData, prefix="plt"):
    flow: str
    platform: str


class FlowCb(CallbackData, prefix="flow"):
    action: str
    platform: str


class PayMethodCb(CallbackData, prefix="paym"):
    method: str


class PayAmountCb(CallbackData, prefix="paya"):
    method: str
    amount: int


class PaymentActionCb(CallbackData, prefix="payact"):
    action: str
    payment_id: int


PLATFORM_LABELS = {
    "android": "Android",
    "ios": "iPhone",
    "windows": "Windows",
    "mac": "Mac",
}

PLATFORM_INSTALL_TEXT = {
    "android": "Установи приложение v2RayTun на Android. После этого нажми «Дальше».",
    "ios": "Установи приложение v2RayTun на iPhone. После этого нажми «Дальше».",
    "windows": "Установи клиент для Windows. После этого нажми «Дальше».",
    "mac": "Установи клиент для Mac. После этого нажми «Дальше».",
}

PAYMENT_METHOD_LABELS = {"platega": "СБП", "cryptobot": "Crypto Bot"}


class BotNavCb(CallbackData, prefix="nav"):
    screen: str


def _make_inline_button(ui: dict, key: str, callback_data: str, *, fallback_text: str | None = None) -> InlineKeyboardButton:
    cfg = get_button_config(ui, key)
    text = cfg.get("text") or fallback_text or key
    payload = {"text": text, "callback_data": callback_data}
    style = (cfg.get("style") or "").strip()
    if style and style != "default":
        payload["style"] = style
    custom_emoji_id = (cfg.get("custom_emoji_id") or "").strip()
    if custom_emoji_id:
        payload["icon_custom_emoji_id"] = custom_emoji_id
    try:
        return InlineKeyboardButton(**payload)
    except Exception:
        return InlineKeyboardButton(text=text, callback_data=callback_data)


async def _safe_edit_message(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        await message.answer(text, reply_markup=reply_markup)


def _days_left(balance_cents: int, daily_spend_cents: int) -> str:
    if daily_spend_cents <= 0:
        return "∞"
    return str(balance_cents // daily_spend_cents)


def _estimated_end_date(balance_cents: int, daily_spend_cents: int) -> str:
    if daily_spend_cents <= 0:
        return "—"
    days = balance_cents // daily_spend_cents
    return (dt.date.today() + dt.timedelta(days=days)).strftime("%d.%m.%Y")


def _format_datetime(value: dt.datetime | None) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m %H:%M")


def _format_remaining_delete(created_at: dt.datetime | None) -> str:
    if not created_at:
        return "можно удалить"
    diff = dt.timedelta(hours=24) - (dt.datetime.utcnow() - created_at)
    if diff.total_seconds() <= 0:
        return "можно удалить"
    total_hours = max(1, int(diff.total_seconds() // 3600) + (1 if diff.total_seconds() % 3600 else 0))
    return f"удалить через ~{total_hours}ч"


def _device_status_line(key: Key, user: User, settings: Settings) -> str:
    parts: list[str] = []
    parts.append("активно" if key.status == KeyStatus.active else "требует пополнения")
    created_at = key.assigned_at or key.created_at
    if created_at and (dt.datetime.utcnow() - created_at) < dt.timedelta(hours=24):
        parts.append("новое")
    parts.append(_format_remaining_delete(created_at))
    if key.last_config_updated_at:
        parts.append(f"конфиг: {_format_datetime(key.last_config_updated_at)}")
    daily = cents_to_rub_str(settings.price_per_key_per_day_cents)
    return f"• {key.name} — {'; '.join(parts)}; {daily} ₽/день"


def _main_menu_text(ui: dict, user: User, settings: Settings, device_count: int) -> str:
    daily_spend_cents = device_count * settings.price_per_key_per_day_cents
    return format_ui_text(
        ui,
        "menu",
        first_name=user.first_name or (user.username or "друг"),
        balance=cents_to_rub_str(user.balance_cents),
        referral_bonus=cents_to_rub_str(settings.referral_bonus_cents),
        days_left=_days_left(user.balance_cents, daily_spend_cents),
        device_count=device_count,
        daily_spend=cents_to_rub_str(daily_spend_cents),
        end_date=_estimated_end_date(user.balance_cents, daily_spend_cents),
    )


def _main_menu_kb(ui: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_make_inline_button(ui, "my_devices", BotNavCb(screen="devices").pack())],
            [_make_inline_button(ui, "deposit", BotNavCb(screen="deposit_methods").pack())],
            [
                _make_inline_button(ui, "ref", BotNavCb(screen="ref").pack()),
                _make_inline_button(ui, "info", BotNavCb(screen="info").pack()),
            ],
        ]
    )


def _first_start_kb(ui: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_make_inline_button(ui, "connect", BotNavCb(screen="first_connect").pack())]])


def _platform_keyboard(ui: dict, flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_make_inline_button(ui, "device_android", PlatformCb(flow=flow, platform="android").pack())],
            [_make_inline_button(ui, "device_ios", PlatformCb(flow=flow, platform="ios").pack())],
            [_make_inline_button(ui, "device_windows", PlatformCb(flow=flow, platform="windows").pack())],
            [_make_inline_button(ui, "device_mac", PlatformCb(flow=flow, platform="mac").pack())],
            [_make_inline_button(ui, "main_menu", BotNavCb(screen="menu").pack())],
        ]
    )


def _next_keyboard(ui: dict, action: str, platform: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_make_inline_button(ui, "next", FlowCb(action=action, platform=platform).pack())],
            [_make_inline_button(ui, "main_menu", BotNavCb(screen="menu").pack())],
        ]
    )


def _confirm_connect_keyboard(ui: dict, platform: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _make_inline_button(ui, "yes", FlowCb(action="confirm_add_yes", platform=platform).pack()),
                _make_inline_button(ui, "no", FlowCb(action="confirm_add_no", platform=platform).pack()),
            ],
            [_make_inline_button(ui, "main_menu", BotNavCb(screen="menu").pack())],
        ]
    )


def _device_list_kb(ui: dict, keys: list[Key]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key in keys:
        rows.append([InlineKeyboardButton(text=key.name, callback_data=DeviceActionCb(action="open", key_id=key.id).pack())])
    rows.append([_make_inline_button(ui, "add_device", BotNavCb(screen="add_device").pack())])
    rows.append([_make_inline_button(ui, "main_menu", BotNavCb(screen="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _device_card_kb(ui: dict, key_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_make_inline_button(ui, "rename_device", DeviceActionCb(action="rename", key_id=key_id).pack())],
            [_make_inline_button(ui, "replace_config", DeviceActionCb(action="replace", key_id=key_id).pack())],
            [_make_inline_button(ui, "delete_device", DeviceActionCb(action="delete", key_id=key_id).pack())],
            [_make_inline_button(ui, "main_menu", BotNavCb(screen="menu").pack())],
        ]
    )


def _rename_cancel_kb(ui: dict, key_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_make_inline_button(ui, "cancel", DeviceActionCb(action="open", key_id=key_id).pack())]])


def _device_key_text(ui: dict, key: Key) -> str:
    return format_ui_text(ui, "device_key_only", config_uri=key.config_uri)


def _device_card_text(ui: dict, key: Key, user: User, settings: Settings) -> str:
    return format_ui_text(
        ui,
        "device_card",
        key_name=key.name,
        key_status="активно" if key.status == KeyStatus.active else "требует пополнения",
        delete_status=_format_remaining_delete(key.assigned_at or key.created_at),
        config_updated_at=_format_datetime(key.last_config_updated_at),
        daily_spend=cents_to_rub_str(settings.price_per_key_per_day_cents),
    )


def _payment_methods_kb(ui: dict, settings: Settings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if settings.platega_merchant_id and settings.platega_secret:
        rows.append([_make_inline_button(ui, "pay_platega", PayMethodCb(method="platega").pack())])
    if settings.cryptobot_token:
        rows.append([_make_inline_button(ui, "pay_cryptobot", PayMethodCb(method="cryptobot").pack())])
    rows.append([_make_inline_button(ui, "main_menu", BotNavCb(screen="menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _payment_amounts_kb(ui: dict, method: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _make_inline_button(ui, "amount_100", PayAmountCb(method=method, amount=100).pack()),
                _make_inline_button(ui, "amount_200", PayAmountCb(method=method, amount=200).pack()),
                _make_inline_button(ui, "amount_300", PayAmountCb(method=method, amount=300).pack()),
            ],
            [_make_inline_button(ui, "amount_custom", PayAmountCb(method=method, amount=0).pack())],
            [_make_inline_button(ui, "main_menu", BotNavCb(screen="menu").pack())],
        ]
    )


def _simple_nav_kb(ui: dict, *keys: str) -> InlineKeyboardMarkup:
    rows = []
    mapping = {
        "my_devices": BotNavCb(screen="devices").pack(),
        "main_menu": BotNavCb(screen="menu").pack(),
        "deposit": BotNavCb(screen="deposit_methods").pack(),
    }
    for key in keys:
        rows.append([_make_inline_button(ui, key, mapping[key])])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_bot(settings: Settings, SessionLocal) -> tuple[Bot, Dispatcher]:
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    router = Router()

    xui: ThreeXUI | None = None
    if settings.xui_base_url and settings.xui_username and settings.xui_password and settings.xui_inbound_id:
        xui = ThreeXUI(
            ThreeXUIConfig(
                base_url=settings.xui_base_url,
                username=settings.xui_username,
                password=settings.xui_password,
                verify_tls=settings.xui_verify_tls,
            )
        )

    def _sub_id() -> str:
        return secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]

    def _xui_missing_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(n in text for n in ["not found", "record not found", "unable to find", "client not found", "failed to find"])

    async def _set_remote_client_enabled(key: Key, enabled: bool) -> None:
        if not (xui and key.xui_inbound_id and key.xui_client_id):
            return
        if not key.xui_client_json:
            return
        try:
            client_obj = json.loads(key.xui_client_json)
        except Exception:
            return
        if not isinstance(client_obj, dict):
            return
        client_obj["enable"] = bool(enabled)
        await asyncio.to_thread(xui.update_client, int(key.xui_inbound_id), str(key.xui_client_id), client_obj)
        key.xui_client_json = json.dumps(client_obj, ensure_ascii=False)

    async def _remember_key_message(tg_user_id: int, chat_id: int, message_id: int) -> None:
        with session_scope(SessionLocal) as session:
            user = session.scalar(select(User).where(User.tg_id == tg_user_id))
            if not user:
                return
            user.current_key_chat_id = chat_id
            user.current_key_message_id = message_id
            session.add(user)

    async def _delete_tracked_key_message(tg_user_id: int, *, exclude_message_id: int | None = None) -> None:
        with session_scope(SessionLocal) as session:
            user = session.scalar(select(User).where(User.tg_id == tg_user_id))
            if not user or not user.current_key_message_id or not user.current_key_chat_id:
                return
            chat_id = int(user.current_key_chat_id)
            message_id = int(user.current_key_message_id)
            if exclude_message_id is not None and message_id == exclude_message_id:
                return
            user.current_key_chat_id = None
            user.current_key_message_id = None
            session.add(user)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

    def _active_user_keys(session, user_id: int) -> list[Key]:
        return list(
            session.scalars(
                select(Key)
                .where(Key.owner_id == user_id, Key.status.in_([KeyStatus.active, KeyStatus.paused]))
                .order_by(Key.created_at.asc(), Key.id.asc())
            )
        )

    async def _sync_deleted_xui_keys_for_user(tg_user_id: int) -> int:
        if not xui:
            return 0
        with session_scope(SessionLocal) as session:
            user = session.scalar(select(User).where(User.tg_id == tg_user_id))
            if not user:
                return 0
            keys = list(
                session.scalars(
                    select(Key).where(
                        Key.owner_id == user.id,
                        Key.status.in_([KeyStatus.active, KeyStatus.paused]),
                        Key.xui_inbound_id.is_not(None),
                        Key.xui_client_id.is_not(None),
                    )
                )
            )
            if not keys:
                return 0
            inbound_to_ids: dict[int, set[str] | None] = {}
            changed = 0
            for inbound_id in sorted({int(k.xui_inbound_id) for k in keys if k.xui_inbound_id}):
                try:
                    inbound_to_ids[inbound_id] = await asyncio.to_thread(xui.list_client_ids, inbound_id)
                except Exception as exc:
                    inbound_to_ids[inbound_id] = set() if _xui_missing_error(exc) else None
            for key in keys:
                remote_ids = inbound_to_ids.get(int(key.xui_inbound_id)) if key.xui_inbound_id else None
                if remote_ids is None:
                    continue
                if str(key.xui_client_id) not in remote_ids:
                    key.status = KeyStatus.revoked
                    session.add(key)
                    changed += 1
            return changed

    def _referral_reward_exists(session, new_user: User) -> bool:
        txs = list(session.scalars(select(Transaction).where(Transaction.type == TxType.referral_bonus)))
        for tx in txs:
            try:
                meta = json.loads(tx.meta_json or "{}")
            except Exception:
                meta = {}
            if int(meta.get("from_user_id", 0) or 0) == new_user.id:
                return True
        return False

    async def _maybe_award_referral_bonus(session, paid_user: User, ui: dict) -> tuple[int | None, str | None]:
        if not paid_user.referrer_id:
            return None, None
        if _referral_reward_exists(session, paid_user):
            return None, None
        referrer = session.scalar(select(User).where(User.id == paid_user.referrer_id))
        if not referrer or referrer.id == paid_user.id:
            return None, None
        referrer.balance_cents += settings.referral_bonus_cents
        session.add(
            Transaction(
                user_id=referrer.id,
                type=TxType.referral_bonus,
                amount_cents=settings.referral_bonus_cents,
                meta_json=json.dumps({"from_user_id": paid_user.id, "from_tg_id": paid_user.tg_id, "anti_abuse": "first_paid_topup"}, ensure_ascii=False),
            )
        )
        return referrer.tg_id, format_ui_text(ui, "referral_rewarded", bonus=cents_to_rub_str(settings.referral_bonus_cents))

    async def _create_key_record(session, user: User, device_name: str) -> Key | None:
        activate = user.balance_cents > 0
        if xui and settings.vless_template and settings.xui_inbound_id:
            client_uuid = gen_uuid()
            short = client_uuid.split("-")[0]
            email = f"tg{user.tg_id}-{short}"
            client_obj = {
                "id": client_uuid,
                "flow": "",
                "email": email,
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
                "tgId": int(user.tg_id),
                "subId": _sub_id(),
                "comment": device_name,
                "reset": 0,
            }
            await asyncio.to_thread(xui.add_client, int(settings.xui_inbound_id), client_obj)
            config_uri = apply_template(settings.vless_template, uuid=client_uuid, name=device_name, email=email)
            key = Key(
                name=device_name,
                protocol="vless",
                config_uri=config_uri,
                status=KeyStatus.active if activate else KeyStatus.paused,
                owner_id=user.id,
                assigned_at=dt.datetime.utcnow(),
                xui_inbound_id=int(settings.xui_inbound_id),
                xui_client_id=client_uuid,
                xui_email=email,
                xui_client_json=json.dumps(client_obj, ensure_ascii=False),
                last_config_updated_at=dt.datetime.utcnow(),
            )
            session.add(key)
            session.flush()
            if not activate:
                try:
                    client_obj["enable"] = False
                    await asyncio.to_thread(xui.update_client, int(settings.xui_inbound_id), client_uuid, client_obj)
                    key.xui_client_json = json.dumps(client_obj, ensure_ascii=False)
                    session.add(key)
                except Exception:
                    pass
            return key
        return None

    async def _create_device_for_user(tg_user_id: int, username: str | None, first_name: str | None, last_name: str | None, platform: str) -> tuple[dict, Key | None, str | None]:
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == tg_user_id))
            if not user:
                user = get_or_create_user(session, tg_user_id, username, first_name, last_name)
            if user.is_banned:
                return ui, None, "⛔️ Аккаунт заблокирован. Напишите в поддержку."
            if user.is_frozen:
                return ui, None, "🧊 Аккаунт заморожен администратором. Напишите в поддержку."
            device_count = len(_active_user_keys(session, user.id)) + 1
            device_name = f"{PLATFORM_LABELS.get(platform, 'Устройство')} {device_count}"
            try:
                key = await _create_key_record(session, user, device_name)
            except Exception:
                return ui, None, format_ui_text(ui, "xui_create_failed")
            if not key:
                return ui, None, format_ui_text(ui, "xui_create_failed")
            return ui, key, None

    async def _show_devices(target: Message, tg_user_id: int, edit: bool = False) -> None:
        deleted_count = await _sync_deleted_xui_keys_for_user(tg_user_id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == tg_user_id))
            user = user or get_or_create_user(session, tg_user_id, None, None, None)
            keys = _active_user_keys(session, user.id)
            device_count = len(keys)
            daily_spend_cents = device_count * settings.price_per_key_per_day_cents
            status_lines = "\n".join(_device_status_line(key, user, settings) for key in keys) if keys else "• пока нет устройств"
            text = format_ui_text(
                ui,
                "my_devices",
                balance=cents_to_rub_str(user.balance_cents),
                device_count=device_count,
                price_per_day=cents_to_rub_str(settings.price_per_key_per_day_cents),
                daily_spend=cents_to_rub_str(daily_spend_cents),
                days_left=_days_left(user.balance_cents, daily_spend_cents),
                end_date=_estimated_end_date(user.balance_cents, daily_spend_cents),
                devices_status_list=status_lines,
            )
            if deleted_count:
                text = f"{format_ui_text(ui, 'sync_deleted_notice')}\n\n{text}"
            kb = _device_list_kb(ui, keys)
        if edit:
            await _safe_edit_message(target, text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)

    async def _show_payment_methods(message: Message | CallbackQuery, tg_user_id: int, edit: bool = True):
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == tg_user_id))
            user = user or get_or_create_user(session, tg_user_id, None, None, None)
            text = format_ui_text(
                ui,
                "deposit_methods",
                balance=cents_to_rub_str(user.balance_cents),
                days_left=_days_left(user.balance_cents, settings.price_per_key_per_day_cents),
            )
            kb = _payment_methods_kb(ui, settings)
        target = message.message if isinstance(message, CallbackQuery) else message
        if edit:
            await _safe_edit_message(target, text, reply_markup=kb)
        else:
            await target.answer(text, reply_markup=kb)

    async def _show_amounts(call: CallbackQuery, method: str):
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            user = user or get_or_create_user(session, call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
            text = format_ui_text(ui, "deposit_amounts", balance=cents_to_rub_str(user.balance_cents))
            kb = _payment_amounts_kb(ui, method)
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        await _safe_edit_message(call.message, text, reply_markup=kb)

    async def _send_payment_link(message: Message, tg_user_id: int, amount_rub: int, method: str):
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == tg_user_id))
            user = user or get_or_create_user(session, tg_user_id, None, None, None)
            user_id = user.id

        payment_provider = None
        payment_payload: dict[str, object] = {}
        pay_url = ""
        external_invoice_id = ""

        if method == "platega" and settings.platega_merchant_id and settings.platega_secret:
            client = PlategaClient(settings.platega_merchant_id, settings.platega_secret, settings.platega_base_url)
            payload_obj = {"tg_id": tg_user_id, "amount_rub": amount_rub, "type": "balance_topup"}
            transaction = await client.create_transaction(
                amount_rub=amount_rub,
                description=f"{settings.platega_description_prefix} на {amount_rub}₽",
                payload=json.dumps(payload_obj, ensure_ascii=False),
                return_url=settings.platega_success_url or f"{settings.public_base_url}/payments/platega/success",
                failed_url=settings.platega_failed_url or f"{settings.public_base_url}/payments/platega/fail",
                payment_method=settings.platega_payment_method,
                currency=settings.payment_currency or "RUB",
            )
            external_invoice_id = str(transaction.get("transactionId") or "")
            pay_url = str(transaction.get("redirect") or "")
            payment_payload = transaction
            payment_provider = PaymentProvider.platega
        elif method == "cryptobot" and settings.cryptobot_token:
            client = CryptoBotClient(settings.cryptobot_token, settings.cryptobot_base_url)
            payload_obj = {"tg_id": tg_user_id, "amount_rub": amount_rub, "type": "balance_topup"}
            invoice = await client.create_invoice(
                amount_rub=amount_rub,
                payload=payload_obj,
                description=f"Пополнение баланса VPN на {amount_rub}₽",
                accepted_assets=settings.cryptobot_accepted_assets,
            )
            external_invoice_id = str(invoice.get("invoice_id") or "")
            pay_url = str(invoice.get("bot_invoice_url") or invoice.get("mini_app_invoice_url") or invoice.get("web_app_invoice_url") or "")
            payment_payload = invoice
            payment_provider = PaymentProvider.cryptobot
        else:
            await message.answer(format_ui_text(ui, "deposit_manual", tg_id=tg_user_id), reply_markup=_main_menu_kb(ui))
            return

        with session_scope(SessionLocal) as session:
            payment = Payment(
                user_id=user_id,
                provider=payment_provider,
                status=PaymentStatus.pending,
                amount_rub=amount_rub,
                amount_cents=amount_rub * 100,
                currency=settings.payment_currency or "RUB",
                external_invoice_id=external_invoice_id,
                pay_url=pay_url,
                payload_json=json.dumps(payment_payload, ensure_ascii=False),
            )
            session.add(payment)
            session.flush()
            rows = [[InlineKeyboardButton(text=get_button_config(ui, "open_payment_link").get("text") or "оплатить", url=pay_url)]]
            if method != "platega":
                rows.append([_make_inline_button(ui, "check_payment", PaymentActionCb(action="check", payment_id=payment.id).pack())])
            rows.append([_make_inline_button(ui, "main_menu", BotNavCb(screen="menu").pack())])
            kb = InlineKeyboardMarkup(inline_keyboard=rows)

        extra = "\n\nПосле оплаты баланс пополнится автоматически." if method == "platega" else ""
        await message.answer(
            format_ui_text(ui, "deposit_link", method_name=PAYMENT_METHOD_LABELS.get(method, method), amount=amount_rub, invoice_url=pay_url) + extra,
            reply_markup=kb,
        )

    async def _apply_paid_payment(payment_id: int) -> tuple[bool, str | None]:
        reward_tg_id = None
        reward_notice = None
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            payment = session.get(Payment, payment_id)
            if not payment:
                return False, None
            if payment.status == PaymentStatus.paid and payment.processed_at:
                return True, None
            user = session.get(User, payment.user_id)
            if not user:
                return False, None
            if payment.status != PaymentStatus.paid:
                payment.status = PaymentStatus.paid
                payment.paid_at = dt.datetime.utcnow()
            if not payment.processed_at:
                add_topup(session, user, int(payment.amount_cents), meta={"provider": payment.provider.value, "invoice_id": payment.external_invoice_id})
                payment.processed_at = dt.datetime.utcnow()
            session.add(payment)
        return True, None

    @router.callback_query(PaymentActionCb.filter(F.action == "check"))
    async def cb_check_payment(call: CallbackQuery, callback_data: PaymentActionCb):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            payment = session.get(Payment, callback_data.payment_id)
            if not payment or not payment.user or payment.user.tg_id != call.from_user.id:
                await call.answer("Платёж не найден", show_alert=True)
                return
            provider = payment.provider
            external_invoice_id = str(payment.external_invoice_id)
            fallback_amount = payment.amount_rub

        if provider == PaymentProvider.platega:
            if not settings.platega_merchant_id or not settings.platega_secret:
                await call.answer("Platega не настроена", show_alert=True)
                return
            client = PlategaClient(settings.platega_merchant_id, settings.platega_secret, settings.platega_base_url)
            try:
                invoice = await client.get_transaction(external_invoice_id)
            except PlategaError:
                await call.answer("Счёт не найден", show_alert=True)
                return
            status = str(invoice.get("status") or "").upper()
            if status == "CONFIRMED":
                with session_scope(SessionLocal) as session:
                    payment = session.get(Payment, callback_data.payment_id)
                    if payment:
                        payment.status = PaymentStatus.paid
                        payment.paid_at = dt.datetime.utcnow()
                        payment.payload_json = json.dumps(invoice, ensure_ascii=False)
                        session.add(payment)
                await _apply_paid_payment(callback_data.payment_id)
                await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
                amount_value = (invoice.get("paymentDetails") or {}).get("amount") or fallback_amount
                await _safe_edit_message(call.message, format_ui_text(ui, "payment_received", amount=amount_value), reply_markup=_main_menu_kb(ui))
            elif status in {"CANCELED", "CHARGEBACKED", "FAILED"}:
                with session_scope(SessionLocal) as session:
                    payment = session.get(Payment, callback_data.payment_id)
                    if payment:
                        payment.status = PaymentStatus.failed
                        payment.payload_json = json.dumps(invoice, ensure_ascii=False)
                        session.add(payment)
                await call.answer(format_ui_text(ui, "payment_failed"), show_alert=True)
            else:
                await call.answer(format_ui_text(ui, "payment_wait"), show_alert=True)
            return

        if provider != PaymentProvider.cryptobot or not settings.cryptobot_token:
            await call.answer("Провайдер оплаты не настроен", show_alert=True)
            return
        client = CryptoBotClient(settings.cryptobot_token, settings.cryptobot_base_url)
        invoice = await client.get_invoice(int(external_invoice_id))
        if not invoice:
            await call.answer("Счёт не найден", show_alert=True)
            return
        status = str(invoice.get("status") or "").lower()
        if status == "paid":
            with session_scope(SessionLocal) as session:
                payment = session.get(Payment, callback_data.payment_id)
                if payment:
                    payment.status = PaymentStatus.paid
                    payment.paid_at = dt.datetime.utcnow()
                    payment.payload_json = json.dumps(invoice, ensure_ascii=False)
                    session.add(payment)
            await _apply_paid_payment(callback_data.payment_id)
            await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
            await _safe_edit_message(call.message, format_ui_text(ui, "payment_received", amount=invoice.get("amount") or fallback_amount), reply_markup=_main_menu_kb(ui))
        elif status == "expired":
            with session_scope(SessionLocal) as session:
                payment = session.get(Payment, callback_data.payment_id)
                if payment:
                    payment.status = PaymentStatus.expired
                    session.add(payment)
            await call.answer(format_ui_text(ui, "payment_expired"), show_alert=True)
        else:
            await call.answer(format_ui_text(ui, "payment_wait"), show_alert=True)

    @router.message(CommandStart())
    async def cmd_start(message: Message):
        args = (message.text or "").split(maxsplit=1)
        start_param = args[1].strip() if len(args) > 1 else None
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            existing = session.scalar(select(User).where(User.tg_id == message.from_user.id))
            is_new_user = existing is None
            user = get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
            if start_param:
                ref = session.scalar(select(User).where(User.referral_code == start_param))
                if ref and ref.id != user.id and user.referrer_id is None:
                    user.referrer_id = ref.id
                    await message.answer(format_ui_text(ui, "referral_applied"))
            if is_new_user:
                add_topup(session, user, 10000, meta={"reason": "welcome_bonus"})
                await message.answer(format_ui_text(ui, "start_first", balance=cents_to_rub_str(user.balance_cents)), reply_markup=_first_start_kb(ui))
            else:
                device_count = len(_active_user_keys(session, user.id))
                await message.answer(_main_menu_text(ui, user, settings, device_count), reply_markup=_main_menu_kb(ui))

    @router.callback_query(BotNavCb.filter(F.screen == "menu"))
    async def cb_menu(call: CallbackQuery):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            user = user or get_or_create_user(session, call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
            device_count = len(_active_user_keys(session, user.id))
            await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
            await _safe_edit_message(call.message, _main_menu_text(ui, user, settings, device_count), reply_markup=_main_menu_kb(ui))

    @router.callback_query(BotNavCb.filter(F.screen == "help"))
    async def cb_help(call: CallbackQuery):
        await call.answer()
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await _safe_edit_message(call.message, format_ui_text(ui, "help"), reply_markup=_main_menu_kb(ui))

    @router.callback_query(BotNavCb.filter(F.screen == "info"))
    async def cb_info(call: CallbackQuery):
        await call.answer()
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await _safe_edit_message(call.message, format_ui_text(ui, "info"), reply_markup=_main_menu_kb(ui))

    @router.callback_query(BotNavCb.filter(F.screen == "ref"))
    async def cb_ref(call: CallbackQuery):
        await call.answer()
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            user = user or get_or_create_user(session, call.from_user.id, call.from_user.username, call.from_user.first_name, call.from_user.last_name)
            link = f"https://t.me/{(await bot.get_me()).username}?start={user.referral_code}"
            text = format_ui_text(ui, "referral", link=link, referral_bonus=cents_to_rub_str(settings.referral_bonus_cents))
        await _safe_edit_message(call.message, text, reply_markup=_main_menu_kb(ui))

    @router.callback_query(BotNavCb.filter(F.screen == "devices"))
    async def cb_devices(call: CallbackQuery):
        await call.answer()
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        await _show_devices(call.message, call.from_user.id, edit=True)

    @router.callback_query(BotNavCb.filter(F.screen == "first_connect"))
    async def cb_first_connect(call: CallbackQuery):
        await call.answer()
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await _safe_edit_message(call.message, format_ui_text(ui, "device_select"), reply_markup=_platform_keyboard(ui, "first"))

    @router.callback_query(BotNavCb.filter(F.screen == "add_device"))
    async def cb_add_device(call: CallbackQuery):
        await call.answer()
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await _safe_edit_message(call.message, format_ui_text(ui, "device_select_add"), reply_markup=_platform_keyboard(ui, "add"))

    @router.callback_query(PlatformCb.filter())
    async def cb_platform(call: CallbackQuery, callback_data: PlatformCb):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        text = format_ui_text(
            ui,
            "platform_install",
            platform=PLATFORM_LABELS.get(callback_data.platform, callback_data.platform),
            instruction=PLATFORM_INSTALL_TEXT.get(callback_data.platform, ""),
        )
        next_action = "first_issue" if callback_data.flow == "first" else "add_confirm"
        await _safe_edit_message(call.message, text, reply_markup=_next_keyboard(ui, next_action, callback_data.platform))

    @router.callback_query(FlowCb.filter(F.action == "add_confirm"))
    async def cb_add_confirm(call: CallbackQuery, callback_data: FlowCb):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        text = format_ui_text(ui, "connect_confirm", price_per_day=cents_to_rub_str(settings.price_per_key_per_day_cents))
        await _safe_edit_message(call.message, text, reply_markup=_confirm_connect_keyboard(ui, callback_data.platform))

    @router.callback_query(FlowCb.filter(F.action == "confirm_add_no"))
    async def cb_confirm_add_no(call: CallbackQuery, callback_data: FlowCb):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await _safe_edit_message(call.message, format_ui_text(ui, "connect_cancelled"), reply_markup=_simple_nav_kb(ui, "my_devices", "main_menu"))

    @router.callback_query(FlowCb.filter(F.action.in_(["first_issue", "confirm_add_yes"])))
    async def cb_issue_device(call: CallbackQuery, callback_data: FlowCb):
        await call.answer()
        ui, key, error = await _create_device_for_user(
            call.from_user.id,
            call.from_user.username,
            call.from_user.first_name,
            call.from_user.last_name,
            callback_data.platform,
        )
        if error or not key:
            await call.message.answer(error or format_ui_text(ui, "xui_create_failed"), reply_markup=_main_menu_kb(ui))
            return
        next_action = "first_connected" if callback_data.action == "first_issue" else "device_connected"
        await _safe_edit_message(call.message, _device_key_text(ui, key), reply_markup=_next_keyboard(ui, next_action, callback_data.platform))
        await _remember_key_message(call.from_user.id, call.message.chat.id, call.message.message_id)
        with session_scope(SessionLocal) as session:
            user = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            card_text = _device_card_text(ui, key, user, settings)
        await call.message.answer(card_text, reply_markup=_device_card_kb(ui, key.id))

    @router.callback_query(FlowCb.filter(F.action.in_(["first_connected", "device_connected"])))
    async def cb_connected(call: CallbackQuery):
        await call.answer()
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            if user:
                user.current_key_chat_id = None
                user.current_key_message_id = None
                session.add(user)
        await _safe_edit_message(call.message, format_ui_text(ui, "device_connected"), reply_markup=_simple_nav_kb(ui, "my_devices", "main_menu"))

    @router.callback_query(DeviceActionCb.filter(F.action == "open"))
    async def cb_device_open(call: CallbackQuery, callback_data: DeviceActionCb):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            key = session.scalar(
                select(Key)
                .where(Key.id == callback_data.key_id, Key.owner.has(tg_id=call.from_user.id), Key.status.in_([KeyStatus.active, KeyStatus.paused]))
            )
            if not key:
                await call.answer(format_ui_text(ui, "key_not_found_alert"), show_alert=True)
                return
            await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
            user = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            await _safe_edit_message(call.message, _device_key_text(ui, key), reply_markup=None)
            session.flush()
            await _remember_key_message(call.from_user.id, call.message.chat.id, call.message.message_id)
            await call.message.answer(_device_card_text(ui, key, user, settings), reply_markup=_device_card_kb(ui, key.id))

    @router.callback_query(DeviceActionCb.filter(F.action == "rename"))
    async def cb_rename(call: CallbackQuery, callback_data: DeviceActionCb, state: FSMContext):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            if user and (user.is_banned or user.is_frozen):
                await call.answer("Редактирование недоступно. Аккаунт заморожен или заблокирован.", show_alert=True)
                return
        await state.set_state(RenameDeviceState.waiting_for_name)
        await state.update_data(key_id=callback_data.key_id)
        await call.message.answer(format_ui_text(ui, "rename_prompt"), reply_markup=_rename_cancel_kb(ui, callback_data.key_id))

    @router.message(RenameDeviceState.waiting_for_name)
    async def rename_submit(message: Message, state: FSMContext):
        data = await state.get_data()
        key_id = int(data.get("key_id", 0) or 0)
        new_name = (message.text or "").strip()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            if not new_name:
                await message.answer(format_ui_text(ui, "rename_prompt"), reply_markup=_rename_cancel_kb(ui, key_id))
                return
            key = session.scalar(select(Key).where(Key.id == key_id, Key.owner.has(tg_id=message.from_user.id), Key.status.in_([KeyStatus.active, KeyStatus.paused])))
            if not key:
                await state.clear()
                await message.answer(format_ui_text(ui, "key_not_found_alert"), reply_markup=_main_menu_kb(ui))
                return
            key.name = new_name[:64]
            session.add(key)
            done = format_ui_text(ui, "rename_done")
            card_text = _device_card_text(ui, key, key.owner, settings)
        await state.clear()
        await message.answer(done)
        await message.answer(card_text, reply_markup=_device_card_kb(ui, key_id))

    async def _replace_key_config(key: Key) -> None:
        if not (xui and settings.vless_template and settings.xui_inbound_id):
            raise RuntimeError("3x-ui is not configured")
        old_client_id = str(key.xui_client_id or "")
        if old_client_id:
            try:
                await asyncio.to_thread(xui.delete_client, int(key.xui_inbound_id), old_client_id)
            except Exception as exc:
                if not _xui_missing_error(exc):
                    raise
        client_uuid = gen_uuid()
        short = client_uuid.split("-")[0]
        email = f"re{short}-{key.owner.tg_id}"
        client_obj = {
            "id": client_uuid,
            "flow": "",
            "email": email,
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": 0,
            "enable": key.status == KeyStatus.active,
            "tgId": int(key.owner.tg_id),
            "subId": _sub_id(),
            "comment": key.name,
            "reset": 0,
        }
        await asyncio.to_thread(xui.add_client, int(settings.xui_inbound_id), client_obj)
        key.config_uri = apply_template(settings.vless_template, uuid=client_uuid, name=key.name, email=email)
        key.xui_client_id = client_uuid
        key.xui_email = email
        key.xui_client_json = json.dumps(client_obj, ensure_ascii=False)
        key.xui_inbound_id = int(settings.xui_inbound_id)

    @router.callback_query(DeviceActionCb.filter(F.action == "replace"))
    async def cb_replace(call: CallbackQuery, callback_data: DeviceActionCb):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            key = session.scalar(
                select(Key)
                .options(joinedload(Key.owner))
                .where(Key.id == callback_data.key_id, Key.owner.has(tg_id=call.from_user.id), Key.status.in_([KeyStatus.active, KeyStatus.paused]))
            )
            if not key:
                await call.answer(format_ui_text(ui, "key_not_found_alert"), show_alert=True)
                return
            if key.owner and (key.owner.is_banned or key.owner.is_frozen):
                await call.answer("Действие недоступно. Аккаунт заморожен или заблокирован.", show_alert=True)
                return
            try:
                await _replace_key_config(key)
                key.last_config_updated_at = dt.datetime.utcnow()
                session.add(key)
            except Exception:
                await call.answer(format_ui_text(ui, "delete_failed"), show_alert=True)
                return
            replace_done = format_ui_text(ui, "replace_done")
            replace_followup = format_ui_text(ui, "replace_followup")
            key_text = _device_key_text(ui, key)
            card_text = _device_card_text(ui, key, key.owner, settings)
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        await _safe_edit_message(call.message, replace_done)
        await call.message.answer(replace_followup)
        key_msg = await call.message.answer(key_text)
        await _remember_key_message(call.from_user.id, key_msg.chat.id, key_msg.message_id)
        await call.message.answer(card_text, reply_markup=_device_card_kb(ui, callback_data.key_id))

    @router.callback_query(DeviceActionCb.filter(F.action == "delete"))
    async def cb_delete(call: CallbackQuery, callback_data: DeviceActionCb):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            key = session.scalar(select(Key).where(Key.id == callback_data.key_id, Key.owner.has(tg_id=call.from_user.id), Key.status.in_([KeyStatus.active, KeyStatus.paused])))
            if not key:
                await call.answer(format_ui_text(ui, "key_not_found_alert"), show_alert=True)
                return
            owner = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            if owner and (owner.is_banned or owner.is_frozen):
                await call.answer("Действие недоступно. Аккаунт заморожен или заблокирован.", show_alert=True)
                return
            created_at = key.assigned_at or key.created_at
            if created_at and (dt.datetime.utcnow() - created_at) < dt.timedelta(hours=24):
                await _safe_edit_message(call.message, format_ui_text(ui, "delete_denied_24h"), reply_markup=_simple_nav_kb(ui, "my_devices", "main_menu"))
                return
            if key.xui_inbound_id and key.xui_client_id and xui:
                try:
                    await asyncio.to_thread(xui.delete_client, int(key.xui_inbound_id), str(key.xui_client_id))
                except Exception as exc:
                    if not _xui_missing_error(exc):
                        await call.answer(format_ui_text(ui, "delete_failed"), show_alert=True)
                        return
            key.status = KeyStatus.revoked
            session.add(key)
            run_hook(settings.vpn_hook_cmd, "revoke", key.name, key.config_uri)
            user = session.scalar(select(User).where(User.tg_id == call.from_user.id))
            keys = _active_user_keys(session, user.id) if user else []
            daily_spend_cents = len(keys) * settings.price_per_key_per_day_cents
            text = format_ui_text(
                ui,
                "delete_done",
                balance=cents_to_rub_str(user.balance_cents if user else 0),
                device_count=len(keys),
                price_per_day=cents_to_rub_str(settings.price_per_key_per_day_cents),
                daily_spend=cents_to_rub_str(daily_spend_cents),
                days_left=_days_left(user.balance_cents if user else 0, daily_spend_cents),
                end_date=_estimated_end_date(user.balance_cents if user else 0, daily_spend_cents),
                devices_status_list="\n".join(_device_status_line(k, user, settings) for k in keys) if user and keys else "• пока нет устройств",
            )
            kb = _device_list_kb(ui, keys)
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        await _safe_edit_message(call.message, text, reply_markup=kb)

    @router.callback_query(BotNavCb.filter(F.screen == "deposit_methods"))
    async def cb_deposit_methods(call: CallbackQuery):
        await call.answer()
        await _delete_tracked_key_message(call.from_user.id, exclude_message_id=call.message.message_id)
        await _show_payment_methods(call, call.from_user.id, edit=True)

    @router.callback_query(PayMethodCb.filter())
    async def cb_pay_method(call: CallbackQuery, callback_data: PayMethodCb, state: FSMContext):
        await call.answer()
        await state.clear()
        await state.update_data(payment_method=callback_data.method)
        await _show_amounts(call, callback_data.method)

    @router.callback_query(PayAmountCb.filter(F.amount == 0))
    async def cb_pay_custom(call: CallbackQuery, callback_data: PayAmountCb, state: FSMContext):
        await call.answer()
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await state.set_state(CustomAmountState.waiting_for_amount)
        await state.update_data(payment_method=callback_data.method)
        await call.message.answer(format_ui_text(ui, "deposit_custom_prompt", min_amount=settings.payment_min_topup_rub), reply_markup=_simple_nav_kb(ui, "main_menu"))

    @router.callback_query(PayAmountCb.filter(F.amount > 0))
    async def cb_pay_amount(call: CallbackQuery, callback_data: PayAmountCb):
        await call.answer()
        await _send_payment_link(call.message, call.from_user.id, callback_data.amount, callback_data.method)

    @router.message(CustomAmountState.waiting_for_amount)
    async def custom_amount_submit(message: Message, state: FSMContext):
        data = await state.get_data()
        method = data.get("payment_method")
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        if not method:
            await state.clear()
            await message.answer(format_ui_text(ui, "deposit_wait_method"), reply_markup=_main_menu_kb(ui))
            return
        raw = (message.text or "").strip().replace("₽", "").replace("р", "").replace(" ", "")
        if not raw.isdigit() or int(raw) < settings.payment_min_topup_rub:
            await message.answer(format_ui_text(ui, "deposit_custom_invalid", min_amount=settings.payment_min_topup_rub))
            return
        amount = int(raw)
        await state.clear()
        await _send_payment_link(message, message.from_user.id, amount, str(method))

    @router.pre_checkout_query()
    async def pre_checkout(pre_checkout_query):
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

    @router.message(F.successful_payment)
    async def successful_payment(message: Message):
        sp = message.successful_payment
        payload = sp.invoice_payload or ""
        parts = payload.split(":")
        tg_id = int(parts[1]) if len(parts) >= 2 and parts[0] == "topup" else message.from_user.id
        amount_cents = sp.total_amount
        extra_notice = None
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == tg_id))
            if not user:
                user = get_or_create_user(session, tg_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
            add_topup(
                session,
                user,
                amount_cents,
                meta={
                    "provider_payment_charge_id": sp.provider_payment_charge_id,
                    "telegram_payment_charge_id": sp.telegram_payment_charge_id,
                },
            )
            reward_tg_id, notice = await _maybe_award_referral_bonus(session, user, ui)
            if reward_tg_id and notice:
                extra_notice = json.dumps({"tg_id": reward_tg_id, "text": notice}, ensure_ascii=False)
        await _delete_tracked_key_message(message.from_user.id)
        await message.answer(format_ui_text(ui, "payment_received", amount=cents_to_rub_str(amount_cents)), reply_markup=_main_menu_kb(ui))
        if extra_notice:
            try:
                payload = json.loads(extra_notice)
                await bot.send_message(int(payload["tg_id"]), str(payload["text"]))
            except Exception:
                pass

    @router.message(Command("help"))
    async def help_cmd(message: Message):
        await _delete_tracked_key_message(message.from_user.id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await message.answer(format_ui_text(ui, "help"), reply_markup=_main_menu_kb(ui))

    @router.message(Command("info"))
    async def info_cmd(message: Message):
        await _delete_tracked_key_message(message.from_user.id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await message.answer(format_ui_text(ui, "info"), reply_markup=_main_menu_kb(ui))

    @router.message(Command("invite"))
    async def invite_cmd(message: Message):
        await _delete_tracked_key_message(message.from_user.id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == message.from_user.id))
            user = user or get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
            link = f"https://t.me/{(await bot.get_me()).username}?start={user.referral_code}"
        await message.answer(format_ui_text(ui, "referral", link=link, referral_bonus=cents_to_rub_str(settings.referral_bonus_cents)), reply_markup=_main_menu_kb(ui))

    @router.message(Command("balance"))
    async def balance_cmd(message: Message):
        await _delete_tracked_key_message(message.from_user.id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
            user = session.scalar(select(User).where(User.tg_id == message.from_user.id))
            user = user or get_or_create_user(session, message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
            device_count = len(_active_user_keys(session, user.id))
            text_out = _main_menu_text(ui, user, settings, device_count)
        await message.answer(text_out, reply_markup=_main_menu_kb(ui))

    @router.message(Command("mykeys"))
    async def mykeys_cmd(message: Message):
        await _delete_tracked_key_message(message.from_user.id)
        await _show_devices(message, message.from_user.id, edit=False)

    @router.message(Command("buy"))
    async def buy_cmd(message: Message):
        await _delete_tracked_key_message(message.from_user.id)
        with session_scope(SessionLocal) as session:
            ui = get_bot_ui(session)
        await message.answer(format_ui_text(ui, "device_select_add"), reply_markup=_platform_keyboard(ui, "add"))

    @router.message(Command("deposit"))
    async def deposit_cmd(message: Message):
        await _delete_tracked_key_message(message.from_user.id)
        await _show_payment_methods(message, message.from_user.id, edit=False)

    dp.include_router(router)
    return bot, dp
