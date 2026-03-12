from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ContentEntry

LEGACY_DEFAULT_TEXTS = {
    "welcome_text": (
        "👋 Привет! Я помогу купить и настроить VPN через <b>v2RayTun</b>.\n\n"
        "Команды:\n"
        "• /balance — баланс\n"
        "• /invite — пригласить друга\n"
        "• /help — помощь\n\n"
        "Нажмите кнопки ниже 👇"
    ),
    "start_ref_applied_text": (
        "✅ Вы перешли по приглашению.\n"
        "Ваш друг получил бонус {bonus} ₽."
    ),
    "menu_title_text": "🏠 Главное меню",
    "help_text": (
        "🧩 Как подключиться:\n"
        "1) Установите v2RayTun\n"
        "2) Получите ключ в боте\n"
        "3) Откройте ссылку / импортируйте ключ\n"
        "4) Нажмите кнопку питания (Подключить)\n\n"
        "Если что-то не получается — напишите администратору вашего сервиса."
    ),
    "ref_text": (
        "👫 Пригласи друга и получи бонус на баланс.\n\n"
        "Твоя ссылка:\n<code>{link}</code>"
    ),
    "balance_text": (
        "💰 Ваш баланс: <b>{balance} ₽</b>\n"
        "🔑 Активных ключей: <b>{active}</b>\n"
        "💸 Стоимость: <b>{price} ₽/день</b> за 1 ключ\n"
        "{days_line}"
    ),
    "balance_days_line_text": "⏳ Примерно хватит на: <b>{days} дн.</b>\n",
    "keys_intro_text": "🔑 Ваши ключи:",
    "select_device_text": "Выберите устройство:",
    "key_message_text": (
        "🔑 <b>{name}</b>\n"
        "📶 Статус: <b>{status}</b>\n\n"
        "🔐 <b>Ключ:</b>\n<code>{config_uri}</code>\n\n"
        "🌐 <b>Ссылка для импорта:</b>\n<code>{share_url}</code>\n\n"
        "📲 <b>Deep link для v2RayTun:</b>\n<code>{deeplink}</code>\n\n"
        "Инструкция:\n"
        "1) Установите приложение\n"
        "2) Импортируйте ключ по ссылке или через deep link\n"
        "3) Нажмите «Подключить»\n\n"
        "Android: {android_url}\n"
        "iPhone/iPad: {ios_url}"
    ),
    "key_created_text": (
        "✅ Ключ добавлен {activation_suffix}.\n\n"
        "🏷 Имя: <b>{name}</b>\n"
        "📶 Статус: <b>{status}</b>\n"
        "🔐 Ключ:\n<code>{config_uri}</code>"
    ),
    "key_created_active_suffix": "и активен",
    "key_created_paused_suffix": "и поставлен на паузу",
    "xui_error_text": (
        "❌ Не удалось создать ключ на сервере (3x-ui). "
        "Проверьте настройки XUI_* в .env и доступ к панели."
    ),
    "no_free_keys_text": "😕 Сейчас нет свободных ключей. Попробуйте позже.",
    "manual_topup_text": (
        "💳 Авто-оплата не настроена.\n"
        "Отправьте оплату администратору и сообщите ваш Telegram ID:\n"
        "<code>{tg_id}</code>"
    ),
    "invoice_link_text": "Оплатите по ссылке:\n{link}",
    "topup_success_text": "✅ Оплата получена. Баланс пополнен на {amount} ₽",
    "key_deleted_text": "🗑 Ключ удалён.",
    "btn_delete_key": "🗑 Удалить",
    "alert_start_first": "Сначала нажмите /start",
    "alert_no_keys": "У вас нет ключей.",
    "alert_key_not_found": "Ключ не найден.",
    "alert_owner_error": "Ошибка владельца.",
    "alert_resume_need_balance": "Пополните баланс, чтобы включить.",
    "alert_paused": "Поставлено на паузу",
    "alert_resumed": "Включено",
    "alert_deleted": "Удалено",
}

LEGACY_DEFAULT_BUTTONS = {
    "main_menu": [
        [
            {"text": "💰 Баланс", "data": "balance"},
            {"text": "🔑 Мои ключи", "data": "my_keys"},
        ],
        [{"text": "➕ Купить ключ", "data": "buy_key"}],
        [{"text": "📲 Открыть в приложении", "data": "open_app"}],
        [
            {"text": "👫 Реферальная программа", "data": "ref"},
            {"text": "🆘 Помощь", "data": "help"},
        ],
        [{"text": "💳 Пополнить баланс", "data": "deposit"}],
    ],
    "device_menu": [
        [
            {"text": "📱 Телефон", "data": "device:phone"},
            {"text": "💻 Компьютер", "data": "device:pc"},
        ],
        [{"text": "⬅️ Назад", "data": "menu"}],
    ],
    "key_actions_active": [
        [
            {"text": "⏸ Пауза", "data": "pause:{key_id}"},
            {"text": "📲 Получить ссылку", "data": "send:{key_id}"},
        ],
        [{"text": "⬅️ Назад", "data": "my_keys"}],
    ],
    "key_actions_paused": [
        [
            {"text": "▶️ Включить", "data": "resume:{key_id}"},
            {"text": "📲 Получить ссылку", "data": "send:{key_id}"},
        ],
        [{"text": "⬅️ Назад", "data": "my_keys"}],
    ],
    "empty_keys_menu": [
        [{"text": "Нет ключей", "data": "noop"}],
        [{"text": "⬅️ Назад", "data": "menu"}],
    ],
    "keys_bottom_menu": [
        [{"text": "⬅️ Назад", "data": "menu"}],
    ],
    "key_message_bottom_menu": [
        [{"text": "⬅️ Назад в меню", "data": "menu"}],
    ],
}

# Values from the previous "hit-style" build. If a user already has these in the DB,
# we replace them once with the legacy defaults so the bot UI rolls back automatically.
HIT_STYLE_TEXTS = {
    "welcome_text": (
        "<b>Привет, {first_name}!</b>\n\n"
        "Подключите VPN бесплатно. Дарим вам <b>100₽ на баланс</b>!\n\n"
        "🚀 высокая скорость\n"
        "🛡 официальный клиент\n"
        "🌍 доступ ко всем сайтам\n"
        "💳 оплата картой и СБП\n"
        "💰 выгодный тариф\n\n"
        "Стоимость 100₽/мес за 1 устройство.\n\n"
        "👫 Пригласите друзей и получите бонусы на баланс.\n\n"
        "⬇️ Жмите кнопку! ⬇️"
    ),
    "after_activation_text": (
        "🎉 <b>Поздравляем, вы активировали аккаунт!</b>\n"
        "<b>{bonus}₽</b> уже на вашем балансе.\n\n"
        "Теперь давайте настроим ваш VPN.\n"
        "Выберите тип вашего устройства:"
    ),
    "keys_intro_text": (
        "Внимание! Баланс расходуется пропорционально количеству созданных в боте устройств (конфигов).\n\n"
        "📱💻 Нажмите на идентификатор устройства в списке, чтобы получить QR и конфиг файл.\n\n"
        "<b>Список ваших устройств:</b>"
    ),
    "device_sent_text": (
        "1. Установите приложение WireGuard или совместимый клиент.\n"
        "2. Нажмите на конфиг файл и импортируйте его в приложение.\n"
        "3. Либо отсканируйте QR-код.\n"
        "4. Включите туннель.\n\n"
        "Один и тот же QR-код и конфиг файл используйте только на одном устройстве."
    ),
    "help_text": (
        "📖 <b>Помощь</b>\n\n"
        "Если не получается подключиться:\n"
        "1. Откройте «Мои устройства»\n"
        "2. Выберите устройство\n"
        "3. Импортируйте конфиг по файлу или QR\n"
        "4. Включите VPN\n\n"
        "Если доступ не появился — проверьте баланс или напишите в поддержку."
    ),
    "ref_text": (
        "👫 <b>Пригласите друзей</b>\n\n"
        "За каждого приглашённого друга вы получите бонус на баланс.\n\n"
        "Ваша ссылка:\n<code>{link}</code>"
    ),
    "deposit_text": (
        "💳 <b>Пополнение баланса</b>\n\n"
        "Ваш баланс: <b>{balance}₽</b>. Выберите сумму для пополнения:"
    ),
    "broadcast_done_text": "Рассылка завершена. Успешно: {ok}, ошибок: {fail}",
}

HIT_STYLE_BUTTONS = {
    "start_connect": [[{"text": "🎉 Подключить VPN 🎉", "data": "buy_key"}]],
    "main_menu": [
        [{"text": "🔑 Мои устройства 📱💻", "data": "my_keys"}],
        [
            {"text": "Пригласить 👫", "data": "ref"},
            {"text": "💰 Пополнить баланс", "data": "deposit"},
        ],
        [{"text": "📖 Помощь", "data": "help"}],
    ],
    "device_menu": [
        [{"text": "📱 Android", "data": "device:android"}],
        [{"text": "📱 iOS (iPhone, iPad)", "data": "device:ios"}],
        [{"text": "⬅️ Назад", "data": "menu"}],
    ],
    "my_keys_menu_bottom": [
        [{"text": "➕ Добавить ещё устройство", "data": "buy_key"}],
        [
            {"text": "Пригласить 👫", "data": "ref"},
            {"text": "📖 Помощь", "data": "help"},
        ],
        [{"text": "💰 Пополнить баланс", "data": "deposit"}],
    ],
    "deposit_amounts": [
        [
            {"text": "100₽", "data": "deposit_amount:100"},
            {"text": "200₽", "data": "deposit_amount:200"},
            {"text": "300₽", "data": "deposit_amount:300"},
        ],
        [
            {"text": "400₽", "data": "deposit_amount:400"},
            {"text": "500₽", "data": "deposit_amount:500"},
            {"text": "700₽", "data": "deposit_amount:700"},
        ],
        [
            {"text": "1000₽", "data": "deposit_amount:1000"},
            {"text": "2000₽", "data": "deposit_amount:2000"},
            {"text": "3000₽", "data": "deposit_amount:3000"},
        ],
        [{"text": "⬅️ Назад", "data": "menu"}],
    ],
}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


DEFAULT_TEXTS = LEGACY_DEFAULT_TEXTS
DEFAULT_BUTTONS = LEGACY_DEFAULT_BUTTONS


def migrate_bot_content_to_legacy(session: Session) -> None:
    for key, legacy_value in LEGACY_DEFAULT_TEXTS.items():
        row = session.scalar(select(ContentEntry).where(ContentEntry.key == key))
        if not row:
            session.add(ContentEntry(key=key, value=legacy_value, kind="text"))
            continue
        hit_value = HIT_STYLE_TEXTS.get(key)
        if hit_value is not None and row.value == hit_value:
            row.value = legacy_value
            row.kind = "text"
            session.add(row)

    for key, legacy_value in LEGACY_DEFAULT_BUTTONS.items():
        row = session.scalar(select(ContentEntry).where(ContentEntry.key == key))
        legacy_dump = _json_dump(legacy_value)
        if not row:
            session.add(ContentEntry(key=key, value=legacy_dump, kind="json"))
            continue
        hit_value = HIT_STYLE_BUTTONS.get(key)
        if hit_value is not None:
            try:
                current = json.loads(row.value or "[]")
            except Exception:
                current = row.value
            if current == hit_value:
                row.value = legacy_dump
                row.kind = "json"
                session.add(row)


def ensure_content_defaults(session: Session) -> None:
    migrate_bot_content_to_legacy(session)
    for key, value in DEFAULT_TEXTS.items():
        if not session.scalar(select(ContentEntry).where(ContentEntry.key == key)):
            session.add(ContentEntry(key=key, value=value, kind="text"))
    for key, value in DEFAULT_BUTTONS.items():
        if not session.scalar(select(ContentEntry).where(ContentEntry.key == key)):
            session.add(ContentEntry(key=key, value=_json_dump(value), kind="json"))


def get_text(session: Session, key: str, **kwargs: Any) -> str:
    row = session.scalar(select(ContentEntry).where(ContentEntry.key == key))
    value = row.value if row and row.value else DEFAULT_TEXTS.get(key, "")
    return value.format(**kwargs)


def get_json(session: Session, key: str):
    row = session.scalar(select(ContentEntry).where(ContentEntry.key == key))
    raw = row.value if row and row.value else json.dumps(DEFAULT_BUTTONS.get(key, []), ensure_ascii=False)
    try:
        return json.loads(raw)
    except Exception:
        return DEFAULT_BUTTONS.get(key, [])


def all_content_entries(session: Session):
    ensure_content_defaults(session)
    rows = list(session.scalars(select(ContentEntry).order_by(ContentEntry.kind, ContentEntry.key)))
    return rows
