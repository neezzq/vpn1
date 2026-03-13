from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import os
import time

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from starlette.middleware.sessions import SessionMiddleware

from .config import load_settings
from .db import make_engine, make_session_factory, session_scope
from .models import Base, User, Key, KeyStatus, Transaction, TxType, Payment, PaymentStatus
from .crud import cents_to_rub_str, add_topup, admin_find_user
from .vpn import make_v2raytun_deeplink
from .migrate import ensure_schema
from .ui_content import BUTTON_FIELDS, MESSAGE_FIELDS, PLACEHOLDERS, BUTTON_STYLE_OPTIONS, get_bot_ui, save_bot_ui
from .three_xui import ThreeXUI, ThreeXUIConfig
from .cryptobot import verify_webhook_signature
from .platega import verify_callback_headers

load_dotenv()
settings = load_settings()

if settings.database_url.startswith("sqlite"):
    os.makedirs("./data", exist_ok=True)

engine = make_engine(settings)
SessionLocal = make_session_factory(engine)
Base.metadata.create_all(bind=engine)
ensure_schema(engine)

app = FastAPI(title="VPN Shop Admin")
app.add_middleware(SessionMiddleware, secret_key=settings.admin_secret_key, https_only=False, same_site="lax")

templates = Jinja2Templates(directory=str(__import__("pathlib").Path(__file__).parent / "templates"))

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


def _daily_spend_cents(device_count: int) -> int:
    return device_count * settings.price_per_key_per_day_cents


def _days_left(balance_cents: int, daily_spend_cents: int) -> str:
    if daily_spend_cents <= 0:
        return "∞"
    return str(balance_cents // daily_spend_cents)


def _estimated_end_date(balance_cents: int, daily_spend_cents: int) -> str:
    if daily_spend_cents <= 0:
        return "—"
    return (dt.date.today() + dt.timedelta(days=balance_cents // daily_spend_cents)).strftime("%d.%m.%Y")


def _format_dt(value):
    if not value:
        return "—"
    if isinstance(value, dt.datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return str(value)


def _user_summary(session, user: User) -> dict:
    keys = list(session.scalars(select(Key).where(Key.owner_id == user.id, Key.status.in_([KeyStatus.active, KeyStatus.paused]))))
    device_count = len(keys)
    daily_spend = _daily_spend_cents(device_count)
    referrer = session.get(User, user.referrer_id) if user.referrer_id else None
    return {
        "user": user,
        "device_count": device_count,
        "active_count": sum(1 for k in keys if k.status == KeyStatus.active),
        "paused_count": sum(1 for k in keys if k.status == KeyStatus.paused),
        "daily_spend": daily_spend,
        "days_left": _days_left(user.balance_cents, daily_spend),
        "end_date": _estimated_end_date(user.balance_cents, daily_spend),
        "paid_status": "платил" if user.last_paid_at else "не платил",
        "referrer_label": (f"@{referrer.username}" if referrer and referrer.username else (str(referrer.tg_id) if referrer else "—")),
        "last_activity": _format_dt(user.last_activity_at),
    }


def is_admin(request: Request) -> bool:
    return bool(request.session.get("admin") is True)


def _admin_telegram_enabled() -> bool:
    return bool(settings.admin_tg_ids or settings.admin_tg_usernames)


def _is_allowed_telegram_admin(tg_id: int | None, username: str | None) -> bool:
    uname = (username or "").lstrip("@").lower()
    if tg_id is not None and tg_id in settings.admin_tg_ids:
        return True
    if uname and uname in settings.admin_tg_usernames:
        return True
    return False


def _telegram_login_widget_bot_username() -> str:
    async def _fetch() -> str:
        bot = Bot(token=settings.bot_token)
        try:
            me = await bot.get_me()
            return me.username or ""
        finally:
            await bot.session.close()

    try:
        return asyncio.run(_fetch())
    except Exception:
        return ""


def _build_telegram_auth_url() -> str:
    return f"{settings.public_base_url}/admin/auth/telegram"


def _verify_telegram_login(data: dict[str, str]) -> bool:
    provided_hash = str(data.get("hash") or "")
    if not provided_hash:
        return False

    auth_date = str(data.get("auth_date") or "0")
    if not auth_date.isdigit():
        return False
    if abs(int(time.time()) - int(auth_date)) > 86400:
        return False

    check_items: list[str] = []
    for key in sorted(k for k in data.keys() if k != "hash"):
        value = data.get(key)
        if value is None:
            continue
        check_items.append(f"{key}={value}")
    data_check_string = "\n".join(check_items)

    secret_key = hashlib.sha256(settings.bot_token.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed_hash, provided_hash)


async def _broadcast_to_all_users(message_html: str) -> dict:
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    success = 0
    failed = 0
    total = 0
    try:
        with session_scope(SessionLocal) as session:
            users = list(session.scalars(select(User).where(User.is_banned == False).order_by(User.id.asc())))  # noqa: E712
        total = len(users)
        for user in users:
            try:
                await bot.send_message(user.tg_id, message_html, disable_web_page_preview=True)
                success += 1
            except Exception:
                failed += 1
    finally:
        await bot.session.close()
    return {"total": total, "success": success, "failed": failed}


async def _send_message_to_user(tg_id: int, message_html: str) -> bool:
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(tg_id, message_html, disable_web_page_preview=True)
        return True
    except Exception:
        return False
    finally:
        await bot.session.close()


def _remote_toggle_key(key: Key, enabled: bool) -> None:
    if not (xui and key.xui_inbound_id and key.xui_client_id and key.xui_client_json):
        return
    try:
        client = json.loads(key.xui_client_json)
    except Exception:
        return
    if not isinstance(client, dict):
        return
    client["enable"] = bool(enabled)
    xui.update_client(int(key.xui_inbound_id), str(key.xui_client_id), client)
    key.xui_client_json = json.dumps(client, ensure_ascii=False)


def _toggle_user_state(user_id: int, mode: str) -> None:
    with session_scope(SessionLocal) as session:
        user = session.get(User, user_id)
        if not user:
            return
        keys = list(session.scalars(select(Key).where(Key.owner_id == user_id, Key.status.in_([KeyStatus.active, KeyStatus.paused]))))
        if mode == "freeze":
            user.is_frozen = not bool(user.is_frozen)
            if user.is_frozen:
                for key in keys:
                    if key.status == KeyStatus.active:
                        key.status = KeyStatus.paused
                    _remote_toggle_key(key, False)
                    session.add(key)
            else:
                if not user.is_banned and user.balance_cents > 0:
                    for key in keys:
                        key.status = KeyStatus.active
                        _remote_toggle_key(key, True)
                        session.add(key)
        elif mode == "ban":
            user.is_banned = not bool(user.is_banned)
            if user.is_banned:
                for key in keys:
                    if key.status == KeyStatus.active:
                        key.status = KeyStatus.paused
                    _remote_toggle_key(key, False)
                    session.add(key)
            else:
                if not user.is_frozen and user.balance_cents > 0:
                    for key in keys:
                        key.status = KeyStatus.active
                        _remote_toggle_key(key, True)
                        session.add(key)
        session.add(user)




def _extract_platega_amount_rub(body: dict) -> int | None:
    raw = body.get("amount")
    if raw in (None, ""):
        details = body.get("paymentDetails") or {}
        if isinstance(details, dict):
            raw = details.get("amount")
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _notify_payment_success(tg_id: int, amount_rub: int) -> None:
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(tg_id, f"✅ Оплата получена. Баланс пополнен на {amount_rub} ₽")
    finally:
        await bot.session.close()


def _apply_paid_payment(payment: Payment) -> tuple[bool, int | None]:
    with session_scope(SessionLocal) as session:
        payment = session.get(Payment, payment.id)
        if not payment:
            return False, None
        user = session.get(User, payment.user_id)
        if not user:
            return False, None
        notify_tg_id = user.tg_id
        if payment.status != PaymentStatus.paid:
            payment.status = PaymentStatus.paid
            payment.paid_at = dt.datetime.utcnow()
        if not payment.processed_at:
            add_topup(session, user, int(payment.amount_cents), meta={"provider": payment.provider.value, "invoice_id": payment.external_invoice_id})
            payment.processed_at = dt.datetime.utcnow()
        session.add(payment)
        return True, notify_tg_id


@app.post("/crypto-bot/webhook/{secret}", include_in_schema=False)
async def cryptobot_webhook(secret: str, request: Request):
    if not settings.cryptobot_token or not settings.cryptobot_webhook_secret or secret != settings.cryptobot_webhook_secret:
        return PlainTextResponse("forbidden", status_code=403)
    raw = await request.body()
    signature = request.headers.get("crypto-pay-api-signature")
    if not verify_webhook_signature(settings.cryptobot_token, raw, signature):
        return PlainTextResponse("bad signature", status_code=403)
    body = json.loads(raw.decode("utf-8"))
    update = body.get("update") or {}
    payload = update.get("payload") or body.get("payload") or {}
    if body.get("update_type") != "invoice_paid" and update.get("status") != "paid":
        return PlainTextResponse("ok")
    invoice_id = str(update.get("invoice_id") or body.get("invoice_id") or "")
    if not invoice_id:
        return PlainTextResponse("ok")
    with session_scope(SessionLocal) as session:
        payment = session.scalar(select(Payment).where(Payment.external_invoice_id == invoice_id))
        if not payment:
            return PlainTextResponse("ok")
        payment.status = PaymentStatus.paid
        payment.paid_at = dt.datetime.utcnow()
        payment.payload_json = json.dumps(body, ensure_ascii=False)
        session.add(payment)
        payment_id = payment.id
        tg_id = payment.user.tg_id if payment.user else None
        amount_rub = payment.amount_rub
    ok, notify_tg_id = _apply_paid_payment(payment)
    if ok and notify_tg_id:
        await _notify_payment_success(notify_tg_id, amount_rub)
    return PlainTextResponse("ok")


@app.post("/payments/platega/webhook/{secret}", include_in_schema=False)
async def platega_webhook(secret: str, request: Request):
    if not settings.platega_callback_secret or secret != settings.platega_callback_secret:
        return PlainTextResponse("forbidden", status_code=403)
    if not settings.platega_merchant_id or not settings.platega_secret:
        return PlainTextResponse("not configured", status_code=503)
    if not verify_callback_headers(settings.platega_merchant_id, settings.platega_secret, request.headers):
        return PlainTextResponse("bad headers", status_code=403)

    body = await request.json()
    transaction_id = str(body.get("id") or "").strip()
    status = str(body.get("status") or "").upper()
    if not transaction_id:
        return PlainTextResponse("ok")

    with session_scope(SessionLocal) as session:
        payment = session.scalar(select(Payment).where(Payment.external_invoice_id == transaction_id))
        if not payment:
            return PlainTextResponse("ok")
        callback_amount = _extract_platega_amount_rub(body)
        if callback_amount is not None and int(callback_amount) != int(payment.amount_rub):
            return PlainTextResponse("amount mismatch", status_code=400)
        payment.payload_json = json.dumps(body, ensure_ascii=False)
        amount_rub = payment.amount_rub
        if payment.processed_at:
            return PlainTextResponse("ok")
        if status == "CONFIRMED":
            payment.status = PaymentStatus.paid
            payment.paid_at = dt.datetime.utcnow()
        elif status in {"CANCELED", "CHARGEBACKED", "FAILED"}:
            payment.status = PaymentStatus.failed
        session.add(payment)

    if status == "CONFIRMED":
        ok, notify_tg_id = _apply_paid_payment(payment)
        if ok and notify_tg_id:
            await _notify_payment_success(notify_tg_id, amount_rub)
    return PlainTextResponse("ok")


@app.get("/payments/platega/success", response_class=HTMLResponse, include_in_schema=False)
def platega_success():
    return HTMLResponse("<h3>Оплата создана</h3><p>Вернитесь в Telegram. Баланс пополнится автоматически после подтверждения оплаты.</p>")


@app.get("/payments/platega/fail", response_class=HTMLResponse, include_in_schema=False)
def platega_fail():
    return HTMLResponse("<h3>Оплата не завершена</h3><p>Вернитесь в Telegram и создайте новый платёж.</p>")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/admin")


@app.get("/k/{token}", response_class=HTMLResponse, include_in_schema=False)
def share_key(request: Request, token: str):
    with session_scope(SessionLocal) as session:
        key = session.scalar(select(Key).where(Key.share_token == token))
        if not key or key.status == KeyStatus.revoked:
            return HTMLResponse("<h3>Key not found or revoked</h3>", status_code=404)

        deeplink = make_v2raytun_deeplink(key.config_uri)
        share_url = f"{settings.public_base_url}/k/{token}"

        return templates.TemplateResponse(
            "share.html",
            {
                "request": request,
                "key": key,
                "deeplink": deeplink,
                "share_url": share_url,
                "raw": key.config_uri,
            },
        )


@app.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
def healthz():
    return "ok"


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_dashboard(request: Request):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    with session_scope(SessionLocal) as session:
        users = session.scalar(select(func.count(User.id))) or 0
        active_keys = session.scalar(select(func.count(Key.id)).where(Key.status == KeyStatus.active)) or 0
        free_keys = session.scalar(select(func.count(Key.id)).where(Key.status == KeyStatus.free)) or 0
        paused_keys = session.scalar(select(func.count(Key.id)).where(Key.status == KeyStatus.paused)) or 0
        revoked_keys = session.scalar(select(func.count(Key.id)).where(Key.status == KeyStatus.revoked)) or 0
        paid_users = session.scalar(select(func.count(User.id)).where(User.last_paid_at.is_not(None))) or 0
        banned_users = session.scalar(select(func.count(User.id)).where(User.is_banned == True)) or 0  # noqa: E712
        frozen_users = session.scalar(select(func.count(User.id)).where(User.is_frozen == True)) or 0  # noqa: E712

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "users": users,
            "active_keys": active_keys,
            "free_keys": free_keys,
            "paused_keys": paused_keys,
            "revoked_keys": revoked_keys,
            "paid_users": paid_users,
            "banned_users": banned_users,
            "frozen_users": frozen_users,
        },
    )


@app.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": error,
            "tg_login_enabled": _admin_telegram_enabled(),
            "tg_bot_username": _telegram_login_widget_bot_username(),
            "tg_auth_url": _build_telegram_auth_url(),
            "admin_tg_ids": sorted(settings.admin_tg_ids),
            "admin_tg_usernames": sorted(settings.admin_tg_usernames),
        },
    )


@app.post("/admin/login", include_in_schema=False)
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == settings.admin_username and password == settings.admin_password:
        request.session["admin"] = True
        request.session["admin_auth_method"] = "password"
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": "Неверный логин или пароль",
            "tg_login_enabled": _admin_telegram_enabled(),
            "tg_bot_username": _telegram_login_widget_bot_username(),
            "tg_auth_url": _build_telegram_auth_url(),
            "admin_tg_ids": sorted(settings.admin_tg_ids),
            "admin_tg_usernames": sorted(settings.admin_tg_usernames),
        },
    )


@app.get("/admin/auth/telegram", include_in_schema=False)
def telegram_login(request: Request):
    params = dict(request.query_params)
    if not _admin_telegram_enabled():
        return RedirectResponse(url="/admin/login?error=Сначала заполните ADMIN_TG_IDS или ADMIN_TG_USERNAMES", status_code=303)
    if not _verify_telegram_login(params):
        return RedirectResponse(url="/admin/login?error=Ошибка проверки входа через Telegram", status_code=303)

    tg_id = int(params.get("id", "0") or 0)
    username = params.get("username")
    first_name = params.get("first_name")
    last_name = params.get("last_name")
    photo_url = params.get("photo_url")

    if not _is_allowed_telegram_admin(tg_id, username):
        return RedirectResponse(url="/admin/login?error=Этот Telegram аккаунт не является админом", status_code=303)

    request.session["admin"] = True
    request.session["admin_auth_method"] = "telegram"
    request.session["admin_tg_id"] = tg_id
    request.session["admin_tg_username"] = username or ""
    request.session["admin_tg_name"] = " ".join(x for x in [first_name, last_name] if x).strip()
    request.session["admin_tg_photo_url"] = photo_url or ""
    request.session["admin_tg_auth_date"] = params.get("auth_date", "")
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/logout", include_in_schema=False)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login")


@app.get("/admin/users", response_class=HTMLResponse, include_in_schema=False)
def users_page(request: Request, q: str = ""):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    with session_scope(SessionLocal) as session:
        if q:
            users = admin_find_user(session, q)
        else:
            users = list(session.scalars(select(User).order_by(User.id.desc()).limit(100)))
        rows = [_user_summary(session, u) for u in users]

    return templates.TemplateResponse(
        "users.html",
        {"request": request, "rows": rows, "q": q, "cents_to_rub_str": cents_to_rub_str},
    )


@app.get("/admin/user/{user_id}", response_class=HTMLResponse, include_in_schema=False)
def user_detail(request: Request, user_id: int):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    with session_scope(SessionLocal) as session:
        user = session.get(User, user_id)
        if not user:
            return HTMLResponse("User not found", status_code=404)
        keys = list(session.scalars(select(Key).where(Key.owner_id == user_id).order_by(Key.id.desc())))
        txs = list(session.scalars(select(Transaction).where(Transaction.user_id == user_id).order_by(Transaction.id.desc()).limit(50)))
        summary = _user_summary(session, user)
    return templates.TemplateResponse(
        "user_detail.html",
        {"request": request, "user": user, "keys": keys, "txs": txs, "summary": summary, "cents_to_rub_str": cents_to_rub_str},
    )


@app.post("/admin/user/{user_id}/topup", include_in_schema=False)
def admin_topup(request: Request, user_id: int, amount_rub: int = Form(...), comment: str = Form("")):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    with session_scope(SessionLocal) as session:
        user = session.get(User, user_id)
        if not user:
            return HTMLResponse("User not found", status_code=404)
        amount_cents = amount_rub * 100
        add_topup(session, user, amount_cents, meta={"comment": comment}, tx_type=TxType.admin_adjust)

    referer = request.headers.get("referer") or f"/admin/user/{user_id}"
    return RedirectResponse(url=referer, status_code=303)


@app.post("/admin/user/{user_id}/toggle-freeze", include_in_schema=False)
def admin_toggle_freeze(request: Request, user_id: int):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")
    _toggle_user_state(user_id, "freeze")
    referer = request.headers.get("referer") or f"/admin/user/{user_id}"
    return RedirectResponse(url=referer, status_code=303)


@app.post("/admin/user/{user_id}/toggle-ban", include_in_schema=False)
def admin_toggle_ban(request: Request, user_id: int):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")
    _toggle_user_state(user_id, "ban")
    referer = request.headers.get("referer") or f"/admin/user/{user_id}"
    return RedirectResponse(url=referer, status_code=303)


@app.post("/admin/user/{user_id}/message", include_in_schema=False)
def admin_message_user(request: Request, user_id: int, message_html: str = Form(...)):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")
    with session_scope(SessionLocal) as session:
        user = session.get(User, user_id)
        if not user:
            return HTMLResponse("User not found", status_code=404)
        tg_id = user.tg_id
    if message_html.strip():
        asyncio.run(_send_message_to_user(tg_id, message_html.strip()))
    referer = request.headers.get("referer") or f"/admin/user/{user_id}"
    return RedirectResponse(url=referer, status_code=303)


@app.get("/admin/keys", response_class=HTMLResponse, include_in_schema=False)
def keys_page(request: Request, status: str = "all"):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    with session_scope(SessionLocal) as session:
        q = select(Key).order_by(Key.id.desc())
        if status != "all":
            try:
                st = KeyStatus(status)
                q = q.where(Key.status == st)
            except Exception:
                pass
        keys = list(session.scalars(q.limit(200)))
    return templates.TemplateResponse("keys.html", {"request": request, "keys": keys, "status": status})


@app.post("/admin/keys/add", include_in_schema=False)
def add_key(request: Request, name: str = Form(...), protocol: str = Form("vless"), config_uri: str = Form(...)):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    with session_scope(SessionLocal) as session:
        key = Key(name=name.strip(), protocol=protocol.strip(), config_uri=config_uri.strip(), status=KeyStatus.free)
        session.add(key)

    return RedirectResponse(url="/admin/keys", status_code=303)


@app.post("/admin/keys/bulk_add", include_in_schema=False)
def bulk_add_keys(request: Request, protocol: str = Form("vless"), lines: str = Form(...)):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    added = 0
    with session_scope(SessionLocal) as session:
        for raw in lines.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            name = f"key-{__import__('datetime').datetime.utcnow().strftime('%Y%m%d')}-{added + 1}"
            session.add(Key(name=name, protocol=protocol.strip(), config_uri=raw, status=KeyStatus.free))
            added += 1
    return RedirectResponse(url="/admin/keys", status_code=303)


@app.get("/admin/bot-ui", response_class=HTMLResponse, include_in_schema=False)
def bot_ui_page(request: Request, saved: int = 0):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    with session_scope(SessionLocal) as session:
        ui = get_bot_ui(session)

    return templates.TemplateResponse(
        "bot_ui.html",
        {
            "request": request,
            "ui": ui,
            "button_fields": BUTTON_FIELDS,
            "message_fields": MESSAGE_FIELDS,
            "placeholders": PLACEHOLDERS,
            "button_style_options": BUTTON_STYLE_OPTIONS,
            "saved": bool(saved),
        },
    )


@app.post("/admin/bot-ui", include_in_schema=False)
async def bot_ui_save(request: Request):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    form = await request.form()

    buttons: dict[str, dict[str, str]] = {}
    messages: dict[str, str] = {}
    for field_key, _ in BUTTON_FIELDS:
        buttons[field_key] = {
            "text": str(form.get(f"btn__{field_key}__text", "")).strip(),
            "style": str(form.get(f"btn__{field_key}__style", "default")).strip() or "default",
            "custom_emoji_id": str(form.get(f"btn__{field_key}__custom_emoji_id", "")).strip(),
        }
    for field_key, _ in MESSAGE_FIELDS:
        messages[field_key] = str(form.get(f"msg__{field_key}", "")).strip()

    with session_scope(SessionLocal) as session:
        save_bot_ui(session, {"buttons": buttons, "messages": messages})

    return RedirectResponse(url="/admin/bot-ui?saved=1", status_code=303)


@app.get("/admin/broadcast", response_class=HTMLResponse, include_in_schema=False)
def broadcast_page(request: Request, sent: int = 0, total: int = 0, success: int = 0, failed: int = 0):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    return templates.TemplateResponse(
        "broadcast.html",
        {
            "request": request,
            "sent": bool(sent),
            "total": total,
            "success": success,
            "failed": failed,
        },
    )


@app.post("/admin/broadcast", include_in_schema=False)
def broadcast_send(request: Request, message_html: str = Form(...)):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login")

    message_html = (message_html or "").strip()
    if not message_html:
        return templates.TemplateResponse(
            "broadcast.html",
            {"request": request, "sent": False, "total": 0, "success": 0, "failed": 0, "error": "Введите сообщение."},
        )

    result = asyncio.run(_broadcast_to_all_users(message_html))
    return RedirectResponse(
        url=f"/admin/broadcast?sent=1&total={result['total']}&success={result['success']}&failed={result['failed']}",
        status_code=303,
    )
