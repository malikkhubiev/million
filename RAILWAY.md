# Деплой на Railway (вместо Render)

Сайт остаётся на Vercel: https://life-energy-phi.vercel.app/  
API / бот / дашборд — этот репозиторий на Railway.

## 1. Создать проект

1. Зайди на https://railway.app → **New Project** → **Deploy from GitHub**.
2. Репозиторий: `malikkhubiev/million` (корень = этот код, не `server`).
3. Railway подхватит `Dockerfile` + `railway.toml`.

Или через CLI:

```bash
npm i -g @railway/cli
railway login
cd bot-payment   # или корень репо million
railway init
railway up
railway domain
```

После `railway domain` получишь URL вида `https://….up.railway.app`.

## 2. Volume для SQLite (обязательно, иначе БД сотрётся)

1. В сервисе → **Settings** → **Volumes** → **Add Volume**.
2. Mount path: `/app/data`
3. `DATABASE_URL` оставь: `sqlite+aiosqlite:///./data/app.db`

Без volume каждый редеплой = пустая база. Для серьёзной нагрузки лучше плагин **Postgres** и URL вида `postgresql+asyncpg://…` (потребуется `asyncpg` в requirements).

## 3. Переменные окружения

Скопируй из `railway.env.example` в **Variables**. Минимум:

| Ключ | Значение |
| --- | --- |
| `APP_BASE_URL` | `https://твой-домен.up.railway.app` |
| `APP_ENV` | `production` |
| `TELEGRAM_MODE` | `webhook` |
| `TELEGRAM_BOT_TOKEN` | токен бота |
| `TELEGRAM_CHANNEL_ID` | id канала |
| `TELEGRAM_WEBHOOK_SECRET` | случайная строка |
| `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` | ключи ЮKassa |
| `METRIKA_MP_TOKEN` | токен Measurement Protocol |
| `SECRET_KEY` | случайная строка |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/app.db` |

После первого деплоя webhook Telegram выставится сам (если `TELEGRAM_MODE=webhook` и верный `APP_BASE_URL`).

## 4. Webhook ЮKassa

В кабинете ЮKassa:

`https://твой-домен.up.railway.app/api/yookassa/webhook`

## 5. Сайт (Vercel)

В `website/index.html` обнови `LIFE_API` на новый Railway URL (сейчас там ещё Render).

Либо на лендинге перед скриптом:

```html
<script>window.LIFE_API = "https://твой-домен.up.railway.app";</script>
```

## 6. Проверка

- Дашборд: `https://твой-домен.up.railway.app/admin/behavior`
- JSON: `/api/behavior/stats`
- Бот: `/start` → «Показать номер» → оплата

## 7. Выключить Render

Когда Railway стабилен:

1. Останови / удали сервис на Render.
2. Убедись, что ЮKassa и сайт больше не бьют в `*.onrender.com`.

## Стоимость

Hobby на Railway обычно дешевле «всегда включённого» Render Web Service; спишь только если выставишь sleep (на Railway по умолчанию сервис живой). Следи за usage в биллинге.
