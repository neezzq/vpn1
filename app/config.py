from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v


def _csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


@dataclass(frozen=True)
class Settings:
    # Telegram
    bot_token: str
    payments_provider_token: str | None
    payment_currency: str
    payment_min_topup_rub: int
    cryptobot_mode: str
    cryptobot_token: str | None
    cryptobot_base_url: str
    cryptobot_webhook_secret: str | None
    cryptobot_accepted_assets: str

    # Platega (SBP)
    platega_base_url: str
    platega_merchant_id: str | None
    platega_secret: str | None
    platega_callback_secret: str | None
    platega_payment_method: int
    platega_success_url: str | None
    platega_failed_url: str | None
    platega_description_prefix: str

    # Admin
    admin_username: str
    admin_password: str
    admin_secret_key: str  # for cookie signing
    admin_tg_ids: set[int]
    admin_tg_usernames: set[str]

    # Database
    database_url: str

    # Business logic
    price_per_key_per_day_cents: int
    referral_bonus_cents: int

    # Public URL for share links (must be reachable from users)
    public_base_url: str

    # Optional: auto-generate VLESS links (only if you have server already set up)
    vless_template: str | None

    # Optional: run external command on key pause/revoke (for real disable on Xray panel)
    vpn_hook_cmd: str | None

    # Optional: 3X-UI panel integration (auto-generate/disable/delete clients)
    xui_base_url: str | None  # e.g. http://1.2.3.4:2053/randompath
    xui_username: str | None
    xui_password: str | None
    xui_inbound_id: int | None
    xui_verify_tls: bool

    # Billing behavior when balance is insufficient: "pause" (disable) or "revoke" (delete)
    billing_insufficient_action: str


def load_settings() -> Settings:
    bot_token = _env("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    admin_tg_ids_raw = _csv_set(_env("ADMIN_TG_IDS"))
    admin_tg_ids = {int(x) for x in admin_tg_ids_raw if x.isdigit()}
    admin_tg_usernames = {x.lstrip("@").lower() for x in _csv_set(_env("ADMIN_TG_USERNAMES"))}

    cryptobot_mode = (_env("CRYPTOBOT_MODE", "testnet") or "testnet").strip().lower()
    if cryptobot_mode not in {"mainnet", "testnet"}:
        cryptobot_mode = "testnet"
    default_cryptobot_base_url = "https://testnet-pay.crypt.bot/api" if cryptobot_mode == "testnet" else "https://pay.crypt.bot/api"
    default_cryptobot_assets = "JET,TON,USDT,BTC,ETH,LTC,BNB,TRX,USDC" if cryptobot_mode == "testnet" else "USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC"

    public_base_url = (_env("PUBLIC_BASE_URL", "http://127.0.0.1:8000") or "http://127.0.0.1:8000").rstrip("/")
    platega_success_url = _env("PLATEGA_SUCCESS_URL") or f"{public_base_url}/payments/platega/success"
    platega_failed_url = _env("PLATEGA_FAILED_URL") or f"{public_base_url}/payments/platega/fail"

    return Settings(
        bot_token=bot_token,
        payments_provider_token=_env("PAYMENTS_PROVIDER_TOKEN"),
        payment_currency=_env("PAYMENT_CURRENCY", "RUB") or "RUB",
        payment_min_topup_rub=int(_env("PAYMENT_MIN_TOPUP_RUB", "100") or "100"),
        cryptobot_mode=cryptobot_mode,
        cryptobot_token=_env("CRYPTOBOT_TOKEN"),
        cryptobot_base_url=_env("CRYPTOBOT_BASE_URL", default_cryptobot_base_url) or default_cryptobot_base_url,
        cryptobot_webhook_secret=_env("CRYPTOBOT_WEBHOOK_SECRET"),
        cryptobot_accepted_assets=_env("CRYPTOBOT_ACCEPTED_ASSETS", default_cryptobot_assets) or default_cryptobot_assets,
        platega_base_url=(_env("PLATEGA_BASE_URL", "https://app.platega.io") or "https://app.platega.io").rstrip("/"),
        platega_merchant_id=_env("PLATEGA_MERCHANT_ID"),
        platega_secret=_env("PLATEGA_SECRET"),
        platega_callback_secret=_env("PLATEGA_CALLBACK_SECRET"),
        platega_payment_method=int(_env("PLATEGA_PAYMENT_METHOD", "2") or "2"),
        platega_success_url=platega_success_url,
        platega_failed_url=platega_failed_url,
        platega_description_prefix=_env("PLATEGA_DESCRIPTION_PREFIX", "Пополнение баланса VPN") or "Пополнение баланса VPN",
        admin_username=_env("ADMIN_USERNAME", "admin") or "admin",
        admin_password=_env("ADMIN_PASSWORD", "change-me") or "change-me",
        admin_secret_key=_env("ADMIN_SECRET_KEY", "change-this-secret") or "change-this-secret",
        admin_tg_ids=admin_tg_ids,
        admin_tg_usernames=admin_tg_usernames,
        database_url=_env("DATABASE_URL", "sqlite:///./data/app.db") or "sqlite:///./data/app.db",
        price_per_key_per_day_cents=int(_env("PRICE_PER_KEY_PER_DAY_CENTS", "300") or "300"),
        referral_bonus_cents=int(_env("REFERRAL_BONUS_CENTS", "5000") or "5000"),
        public_base_url=public_base_url,
        vless_template=_env("VLESS_TEMPLATE"),
        vpn_hook_cmd=_env("VPN_HOOK_CMD"),
        xui_base_url=(_env("XUI_BASE_URL") or None),
        xui_username=(_env("XUI_USERNAME") or None),
        xui_password=(_env("XUI_PASSWORD") or None),
        xui_inbound_id=int(_env("XUI_INBOUND_ID", "0") or "0") or None,
        xui_verify_tls=(_env("XUI_VERIFY_TLS", "true") or "true").lower() not in ("0", "false", "no"),
        billing_insufficient_action=(_env("BILLING_INSUFFICIENT_ACTION", "pause") or "pause").lower(),
    )
