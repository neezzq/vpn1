from __future__ import annotations

import copy
import datetime as dt
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AppSetting

DEFAULT_BUTTON_STYLE = "default"
BUTTON_STYLE_OPTIONS = ["default", "primary", "success", "danger"]


def _button(text: str, style: str = DEFAULT_BUTTON_STYLE, custom_emoji_id: str = "") -> dict[str, str]:
    return {"text": text, "style": style, "custom_emoji_id": custom_emoji_id}


DEFAULT_BOT_UI: dict[str, Any] = {
    "buttons": {
        "connect": _button("подключиться", style="success"),
        "my_devices": _button("мои устройства"),
        "deposit": _button("пополнить баланс", style="success"),
        "ref": _button("рефка"),
        "help": _button("поддержка"),
        "info": _button("информация"),
        "device_android": _button("андроид"),
        "device_ios": _button("айфон"),
        "device_windows": _button("винда"),
        "device_mac": _button("мак"),
        "next": _button("дальше", style="primary"),
        "yes": _button("да", style="success"),
        "no": _button("нет", style="danger"),
        "cancel": _button("отменить"),
        "add_device": _button("добавить устройство", style="success"),
        "rename_device": _button("поменять название"),
        "replace_config": _button("заменить конфигурацию"),
        "delete_device": _button("удалить", style="danger"),
        "main_menu": _button("главное меню"),
        "pay_platega": _button("⚡ СБП", style="success"),
        "pay_cryptobot": _button("💎 Crypto Bot", style="success"),
        "check_payment": _button("🔄 Проверить оплату"),
        "amount_100": _button("100₽", style="primary"),
        "amount_200": _button("200₽", style="primary"),
        "amount_300": _button("300₽", style="primary"),
        "amount_custom": _button("Введите свою сумму"),
        "open_payment_link": _button("оплатить", style="success"),
    },
    "messages": {
        "start_first": "привет подключайся к нашему ахуенному впн дарим 100р на баланс",
        "menu": (
            "Имя, твой баланс {balance}р. (~{days_left} дней), аккаунт активен\n\n"
            "устройств: {device_count} • списывается: {daily_spend}р/день • ориентировочно до {end_date}\n\n"
            "приглашай друзей и получи 50р за каждого! а твои кенты получат сотку на балик"
        ),
        "help": "Напиши в поддержку, если нужна помощь с подключением или оплатой.",
        "info": (
            "💡 <b>Помощь и контакты</b>\n\n"
            "Если есть вопросы — пишите <a href=\"https://t.me/neezzy\">@neezzy</a>\n\n"
            "🔒 <b>Политика конфиденциальности:</b> "
            "<a href=\"https://telegra.ph/Politika-konfidencialnosti-08-15-17\">читать</a>\n"
            "📜 <b>Пользовательское соглашение:</b> "
            "<a href=\"https://telegra.ph/Polzovatelskoe-soglashenie-08-15-10\">читать</a>"
        ),
        "referral": (

            "приглашай друзей и получи {referral_bonus}р за каждого.\n\n"
            "твоя ссылка:\n<code>{link}</code>\n\n"
            "Бонус начислим после первого успешного пополнения друга."
        ),
        "referral_applied": "✅ Реферальная ссылка сохранена. Бонус активируется после первого пополнения.",
        "referral_rewarded": "🎁 Друг оплатил VPN — тебе начислен бонус {bonus} ₽.",
        "device_select": "100 р уже на твоем балансе щас настраиваться выбери свое устройство",
        "device_select_add": "выбери свое устройство для подключения",
        "platform_install": "инструкция в зависимости от устройства без ключа\n\n{instruction}",
        "connect_confirm": (
            "баланс расходуется в зависимости от кол-ва устройств.\n"
            "одно устройство - {price_per_day}р в день.\n\n"
            "подключить устройство?"
        ),
        "connect_cancelled": "вы отказались от подключения устройства",
        "device_key_only": "ключ и инструкция куда его вставить\n\n<code>{config_uri}</code>",
        "device_card": "{key_name}\n\nстатус: {key_status}\n{delete_status}\nпоследняя замена конфига: {config_updated_at}\nсписание: {daily_spend} ₽/день",
        "device_connected": "поздравляем устройство подключено",
        "my_devices": (
            "балик расходуется в зависимости от кол-ва устройств. {price_per_day} руб/день каждое.\n"
            "подключено устройств: {device_count}\n"
            "общее списание: {daily_spend} ₽/день\n"
            "хватит примерно на: {days_left} дней\n"
            "ориентировочное отключение: {end_date}\n\n"
            "вот твои устройства:\n{devices_status_list}"
        ),
        "rename_prompt": "введите новое название для устройства",
        "rename_done": "название изменено успешно",
        "replace_done": "конфигурация изменена успешно",
        "replace_followup": "новый ключ и инструкция куда его вставить",
        "delete_denied_24h": "сорри бро удалить устройство можно только через 24 часа после создания",
        "delete_done": (
            "устройство успешно удалено\n\n"
            "балик расходуется в зависимости от кол-ва устройств. {price_per_day} руб/день каждое.\n"
            "подключено устройств: {device_count}\n"
            "общее списание: {daily_spend} ₽/день\n"
            "хватит примерно на: {days_left} дней\n"
            "ориентировочное отключение: {end_date}\n\n"
            "вот твои устройства:\n{devices_status_list}"
        ),
        "deposit_methods": (
            "Ваш баланс {balance}₽ (~{days_left} дней), аккаунт активен\n\n"
            "Выберите способ платежа.\n\n"
            "Crypto Bot принимает TON, USDT, BTC и другие монеты. После оплаты баланс зачислится автоматически или по кнопке проверки."
        ),
        "deposit_amounts": (
            "Тариф 100р/мес за 1 устройство.\n\n"
            "Пополнение баланса является однократной операцией (не подписка).\n"
            "Мы не имеем доступа к вашим личным и платежным данным.\n\n"
            "Условия использования\n\n"
            "В случае возникновения проблем обращайтесь в чат поддержки.\n\n"
            "Ваш баланс {balance}₽. Выберите сумму для пополнения баланса:"
        ),
        "deposit_custom_prompt": "Введите сумму пополнения в рублях. Минимум — {min_amount}₽.",
        "deposit_custom_invalid": "Введите сумму числом. Минимум — {min_amount}₽.",
        "deposit_wait_method": "Выбери способ оплаты.",
        "deposit_manual": "💳 Авто-оплата не настроена. Отправьте оплату администратору и сообщите ваш Telegram ID:\n<code>{tg_id}</code>",
        "deposit_link": (
            "Для оплаты через {method_name} нажмите кнопку ниже.\n\n"
            "Сумма: {amount}₽\n"
            "Если баланс не зачислился сразу, нажмите «Проверить оплату».\n{invoice_url}"
        ),
        "payment_received": "✅ Оплата получена. Баланс пополнен на {amount} ₽",
        "payment_wait": "Платёж ещё не найден. Если вы уже оплатили, подождите 10–30 секунд и нажмите проверку ещё раз.",
        "payment_expired": "Счёт истёк. Создай новый платёж.",
        "payment_failed": "Платёж отменён или отклонён. Создай новый платёж.",
        "xui_create_failed": "❌ Не удалось создать ключ на сервере (3x-ui). Проверьте настройки XUI_*.",
        "key_not_found_alert": "Устройство не найдено.",
        "delete_failed": "Не удалось выполнить действие в 3x-ui. Попробуйте позже.",
        "sync_deleted_notice": "Некоторые устройства были удалены на сервере 3x-ui и скрыты из списка.",
    },
}

BUTTON_FIELDS = [
    ("connect", "Первый запуск → подключиться"),
    ("my_devices", "Главное меню → мои устройства"),
    ("deposit", "Главное меню → пополнить баланс"),
    ("ref", "Главное меню → рефка"),
    ("help", "Главное меню → поддержка"),
    ("info", "Главное меню → информация"),
    ("device_android", "Выбор устройства → андроид"),
    ("device_ios", "Выбор устройства → айфон"),
    ("device_windows", "Выбор устройства → винда"),
    ("device_mac", "Выбор устройства → мак"),
    ("next", "Кнопка дальше"),
    ("yes", "Кнопка да"),
    ("no", "Кнопка нет"),
    ("cancel", "Кнопка отменить"),
    ("add_device", "Мои устройства → добавить устройство"),
    ("rename_device", "Карточка устройства → поменять название"),
    ("replace_config", "Карточка устройства → заменить конфигурацию"),
    ("delete_device", "Карточка устройства → удалить"),
    ("main_menu", "Кнопка главное меню"),
    ("pay_platega", "Пополнение → СБП / Platega"),
    ("pay_cryptobot", "Пополнение → Crypto Bot"),
    ("check_payment", "Проверить оплату"),
    ("amount_100", "Сумма 100₽"),
    ("amount_200", "Сумма 200₽"),
    ("amount_300", "Сумма 300₽"),
    ("amount_custom", "Введите свою сумму"),
    ("open_payment_link", "Кнопка оплатить"),
]

MESSAGE_FIELDS = [
    ("start_first", "Сообщение первого запуска"),
    ("menu", "Главное меню"),
    ("help", "Поддержка"),
    ("info", "Информация и документы"),
    ("referral", "Реферальное сообщение"),
    ("referral_applied", "Реферальная ссылка сохранена"),
    ("referral_rewarded", "Реферальный бонус начислен"),
    ("device_select", "Выбор устройства при первом запуске"),
    ("device_select_add", "Выбор устройства при добавлении"),
    ("platform_install", "Инструкция без ключа"),
    ("connect_confirm", "Подтверждение подключения"),
    ("connect_cancelled", "Отказ от подключения"),
    ("device_key_only", "Ключ и инструкция"),
    ("device_card", "Карточка устройства"),
    ("device_connected", "Устройство подключено"),
    ("my_devices", "Мои устройства"),
    ("rename_prompt", "Запрос нового названия"),
    ("rename_done", "Название изменено"),
    ("replace_done", "Конфигурация заменена"),
    ("replace_followup", "Новый ключ и инструкция"),
    ("delete_denied_24h", "Удаление запрещено 24 часа"),
    ("delete_done", "Удаление прошло успешно"),
    ("deposit_methods", "Способы оплаты"),
    ("deposit_amounts", "Суммы пополнения"),
    ("deposit_custom_prompt", "Ввод своей суммы"),
    ("deposit_custom_invalid", "Ошибка своей суммы"),
    ("deposit_wait_method", "Сперва выбрать метод оплаты"),
    ("deposit_manual", "Ручное пополнение"),
    ("deposit_link", "Ссылка на оплату"),
    ("payment_received", "Оплата получена"),
    ("payment_wait", "Платёж ещё не найден"),
    ("payment_expired", "Платёж истёк"),
    ("payment_failed", "Платёж отменён/отклонён"),
    ("xui_create_failed", "Ошибка создания ключа"),
    ("key_not_found_alert", "Устройство не найдено"),
    ("delete_failed", "Ошибка 3x-ui"),
    ("sync_deleted_notice", "Удалено на стороне 3x-ui"),
]


class SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


PLACEHOLDERS: dict[str, list[str]] = {
    "menu": ["balance", "first_name", "referral_bonus", "days_left", "device_count", "daily_spend", "end_date"],
    "referral": ["link", "referral_bonus"],
    "referral_applied": ["bonus"],
    "referral_rewarded": ["bonus"],
    "platform_install": ["platform", "instruction"],
    "connect_confirm": ["price_per_day"],
    "device_key_only": ["config_uri"],
    "device_card": ["key_name", "key_status", "delete_status", "config_updated_at", "daily_spend"],
    "my_devices": ["balance", "device_count", "price_per_day", "daily_spend", "days_left", "end_date", "devices_status_list"],
    "delete_done": ["balance", "device_count", "price_per_day", "daily_spend", "days_left", "end_date", "devices_status_list"],
    "deposit_methods": ["balance", "days_left"],
    "deposit_amounts": ["balance"],
    "deposit_custom_prompt": ["min_amount"],
    "deposit_custom_invalid": ["min_amount"],
    "deposit_manual": ["tg_id"],
    "deposit_link": ["method_name", "amount", "invoice_url"],
    "payment_received": ["amount"],
    "payment_wait": [],
    "payment_expired": [],
    "payment_failed": [],
    "info": [],
}


def _normalize_button(raw: Any, fallback_text: str) -> dict[str, str]:
    if isinstance(raw, str):
        return _button(raw)
    if isinstance(raw, dict):
        return {
            "text": str(raw.get("text", fallback_text) or fallback_text),
            "style": str(raw.get("style", DEFAULT_BUTTON_STYLE) or DEFAULT_BUTTON_STYLE),
            "custom_emoji_id": str(raw.get("custom_emoji_id", "") or ""),
        }
    return _button(fallback_text)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_bot_ui(session: Session) -> dict[str, Any]:
    setting = session.scalar(select(AppSetting).where(AppSetting.key == "bot_ui"))
    if not setting or not setting.value_json:
        return copy.deepcopy(DEFAULT_BOT_UI)
    try:
        raw = json.loads(setting.value_json)
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    merged = _deep_merge(DEFAULT_BOT_UI, raw)
    buttons = merged.get("buttons", {})
    normalized_buttons: dict[str, dict[str, str]] = {}
    for key, default in DEFAULT_BOT_UI["buttons"].items():
        normalized_buttons[key] = _normalize_button(buttons.get(key), default["text"])
    merged["buttons"] = normalized_buttons
    return merged


def save_bot_ui(session: Session, data: dict[str, Any]) -> None:
    payload = _deep_merge(DEFAULT_BOT_UI, data)
    setting = session.scalar(select(AppSetting).where(AppSetting.key == "bot_ui"))
    if not setting:
        setting = AppSetting(key="bot_ui")
        session.add(setting)
    setting.value_json = json.dumps(payload, ensure_ascii=False, indent=2)
    setting.updated_at = dt.datetime.utcnow()
    session.add(setting)


def format_ui_text(ui: dict[str, Any], key: str, **kwargs: Any) -> str:
    template = ui.get("messages", {}).get(key) or DEFAULT_BOT_UI["messages"].get(key, "")
    return str(template).format_map(SafeDict(**kwargs))


def get_button_config(ui: dict[str, Any], key: str) -> dict[str, str]:
    default = DEFAULT_BOT_UI["buttons"].get(key, _button(key))
    raw = ui.get("buttons", {}).get(key, default)
    return _normalize_button(raw, default["text"])
