# Быстрый запуск на твоём сервере

1. Распакуй архив.
2. Скопируй `.env.example` в `.env`.
3. Заполни минимум:
   - `BOT_TOKEN`
   - `XUI_USERNAME`
   - `XUI_PASSWORD`
   - `ADMIN_PASSWORD`
   - `ADMIN_SECRET_KEY`
4. Запусти:

```bash
docker compose up -d --build
```

5. Проверь:

```bash
docker compose logs -f bot
docker compose logs -f web
```

6. Админка будет доступна на:

```text
http://77.83.85.51:8000/admin
```

## Что уже зашито в шаблон

- IP сервера: `77.83.85.51`
- 3x-ui base URL: `https://77.83.85.51:18129/gn0OYG752La2JkU0Y`
- Inbound ID по умолчанию: `1`
- Шаблон VLESS ссылки: порт `31119`, `tcp + tls`
- При нехватке баланса ключ ставится на паузу: `BILLING_INSUFFICIENT_ACTION=pause`

## Что я исправил в архиве

- убраны битые строки `\` в файлах проекта
- исправлен `app/bot_app.py`, чтобы проект компилировался
- добавлен `.env.example` под твой сервер
- добавлен этот `DEPLOY.md`


## Crypto Bot testnet

Для тестовых платежей используй `@CryptoTestnetBot` и API base URL `https://testnet-pay.crypt.bot/api`.
Если укажешь `CRYPTOBOT_MODE=testnet`, проект сам подставит testnet URL по умолчанию.
Для быстрого старта можно взять файл `.env.testnet.example`.


## Platega / СБП

Заполни в `.env`:

- `PLATEGA_MERCHANT_ID`
- `PLATEGA_SECRET`
- `PLATEGA_CALLBACK_SECRET`
- `PUBLIC_BASE_URL` — обязательно HTTPS-домен

Потом в кабинете Platega укажи callback URL:

```text
https://<твой-домен>/payments/platega/webhook/<PLATEGA_CALLBACK_SECRET>
```

После изменения `.env` перезапусти сервисы:

```bash
docker compose up -d --build
```
