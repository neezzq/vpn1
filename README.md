# VPN Shop Bot (MVP)

Минимальная рабочая версия:

- Telegram-бот для пользователей: баланс, добавление ключей из пула, выдача ссылки/Deep Link для импорта в **v2RayTun**.
- Админка (Web): просмотр пользователей, балансов, ключей, транзакций; ручная корректировка баланса; добавление ключей (одиночно и bulk).

> ⚠️ ВАЖНО: Этот проект **не разворачивает Xray/VLESS сервер**.  
> Он может работать в двух режимах:
> 1) **Пул ключей** (как в исходном MVP): вы вручную добавляете `vless://...`/subscription URL в админке.
> 2) **Автогенерация через 3X-UI API**: бот сам создаёт клиента в 3x-ui и отдаёт ссылку пользователю.

---

## 1) Требования

- VPS (Ubuntu/Debian)
- Docker + Docker Compose

---

## 2) Установка

```bash
# 1) распаковать проект
unzip vpn_shop_bot.zip
cd vpn_shop_bot

# 2) создать .env
cp .env.example .env
# или открой DEPLOY.md для быстрого старта на текущем сервере
nano .env
```

Заполните минимум:

- `BOT_TOKEN` — токен вашего телеграм-бота (BotFather)
- `PUBLIC_BASE_URL` — публичный URL вашего сервера (например https://vpn.example.com)
- `ADMIN_PASSWORD` — пароль админки
- `ADMIN_SECRET_KEY` — длинная случайная строка
- `DATABASE_URL` — оставьте как в примере, если используете docker-compose

Запуск:

```bash
docker compose up -d --build
```

Проверка:

- Админка: `http(s)://<PUBLIC_BASE_URL>/admin`
- Healthcheck: `http(s)://<PUBLIC_BASE_URL>/healthz`

---

## 3) Добавление ключей

1. Откройте админку → **Ключи**
2. Вставьте `vless://...` (или `https://...` subscription link)
3. Нажмите “Добавить” (или Bulk add)

После этого пользователи смогут нажать в боте **➕ Добавить ключ** и получить доступ.

---

## 3.1) Автогенерация ключей через 3X-UI API (рекомендуется)

В `.env` заполните:

- `VLESS_TEMPLATE` — шаблон ссылки (обязателен `{uuid}`, можно `{name}`, `{email}`)
- `XUI_BASE_URL` — базовый URL панели 3x-ui, включая web base path (например `http://1.2.3.4:2053/randompath`)
- `XUI_USERNAME` / `XUI_PASSWORD` — логин/пароль администратора 3x-ui
- `XUI_INBOUND_ID` — ID inbound, куда добавлять клиентов

После этого кнопка **➕ Добавить ключ** будет создавать клиента через API `/panel/api/inbounds/addClient`.

Про автоудаление при окончании подписки/денег:

- `BILLING_INSUFFICIENT_ACTION=revoke` — при недостатке баланса ключ **удалится** (и клиент удалится из 3x-ui)
- `BILLING_INSUFFICIENT_ACTION=pause` — при недостатке баланса ключ станет на паузу (и будет disabled в 3x-ui)

---

## 4) Как пользователь подключается

Бот выдаёт:

- `https://<PUBLIC_BASE_URL>/k/<token>` — страница с кнопкой “Открыть в v2RayTun”
- `v2raytun://import/...` — deep link для импорта в приложение
- raw `vless://...` (на странице /k/…)

---

## 5) Платежи (опционально)

По умолчанию кнопка “Пополнить баланс” показывает Telegram ID пользователя, чтобы вы пополняли баланс вручную через админку.

Если вы хотите автоматическую оплату через Platega (СБП):
- Укажите `PLATEGA_MERCHANT_ID` и `PLATEGA_SECRET`
- Укажите `PLATEGA_CALLBACK_SECRET`
- Убедитесь, что `PUBLIC_BASE_URL` доступен по HTTPS
- В кабинете Platega пропишите callback URL: `https://<ваш-домен>/payments/platega/webhook/<PLATEGA_CALLBACK_SECRET>`

Если вы хотите автоматическую оплату через Telegram Payments:
- Укажите `PAYMENTS_PROVIDER_TOKEN` (получается после настройки платежей у BotFather и провайдера)

---

## 6) Где что лежит

- `app/bot_runner.py` — запуск бота (long-polling) + ежедневное списание в 00:05
- `app/web_app.py` — админка и /k/{token}
- `app/models.py` — модели БД
- `app/crud.py` — логика баланса/списаний/пула ключей

---

## 7) Частые правки

### Изменить цену (например 5 ₽/день)
`PRICE_PER_KEY_PER_DAY_CENTS=500`

### Изменить бонус за приглашение (например 30 ₽)
`REFERRAL_BONUS_CENTS=3000`

### Реально отключать ключи на сервере
Если вы НЕ используете 3x-ui API, сделайте скрипт и укажите:
`VPN_HOOK_CMD=/app/scripts/vpn_hook.sh --action {action} --key {key_name} --uri {config_uri}`

---

## Лицензия

MIT (для вашего использования).


## Crypto Bot testnet

Для тестовых платежей используй `@CryptoTestnetBot` и API base URL `https://testnet-pay.crypt.bot/api`.
Если укажешь `CRYPTOBOT_MODE=testnet`, проект сам подставит testnet URL по умолчанию.
Для быстрого старта можно взять файл `.env.testnet.example`.


## 5.1) Platega / СБП

Бот поддерживает создание SBP-платежа через `POST /transaction/process`, ручную проверку статуса через `GET /transaction/{id}` и callback от Platega. Для авторизации используются заголовки `X-MerchantId` и `X-Secret`. Callback от Platega должен быть доступен по публичному HTTPS URL с валидным SSL-сертификатом.
