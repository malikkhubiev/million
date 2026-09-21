# Бот «Верни себе себя»

FastAPI, ЮKassa, Telegram-бот, БД. Сайт — отдельно: [life_energy](https://github.com/malikkhubiev/life_energy).

Клиент на сайте нажимает одну кнопку → Telegram (`?start=site`). Бот просит номер телефона, создаёт оплату в ЮKassa и после `payment.succeeded` сам присылает пригласительную в закрытый канал.

После номера также показывается VIP: персональное обучение за 200 000 ₽ (сначала диагностика 10 000 ₽ за час созвона, затем 190 000 ₽ за 2 недели). Для отладки оплат используйте тестовый магазин ЮKassa (`test_…` ключ).

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Локально: `TELEGRAM_MODE=polling`.  
Прод: `TELEGRAM_MODE=webhook`.

Узнать id канала: `python -m app.scripts.discover_channel`  
Проверить invite: `python -m app.scripts.test_invite`

## Railway (прод)

Пошагово: **[RAILWAY.md](./RAILWAY.md)**.

Кратко:

- Build: `Dockerfile` (Python 3.12.10)
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Volume: `/app/data` (иначе SQLite сотрётся при редеплое)
- `APP_BASE_URL=https://….up.railway.app`, `TELEGRAM_MODE=webhook`
- Webhook ЮKassa: `https://….up.railway.app/api/yookassa/webhook`
- После деплоя обнови `LIFE_API` на лендинге (Vercel)

Шаблон переменных: `railway.env.example`.

## Render (устарело)

Раньше сервис жил на `*.onrender.com`. Конфиг `render.yaml` оставлен для истории — новый прод только на Railway.

## Архитектура оплаты

```
сайт → t.me/bot?start=site
         ↓
   «Показать номер» (request_contact)
         ↓ phone → metadata ЮKassa
   «Генерируем кнопку Оплаты...»
         ↓
   кнопка «Оплатить 50 000 ₽» + «VIP Персональное обучение»
         ↓ webhook payment.succeeded
   программа → createChatInviteLink
   VIP диагностика / обучение → сообщение в Telegram (без инвайта)
```

Даты набора и старта задаются переменными `ENROLLMENT_DEADLINE` и `COURSE_START_DATE`.

Яндекс.Метрика `112323537`. Цели Директа: `view_offer` (50), `tg_click` (30), `bot_started` (150), `show_phone` (1000), `payment_started` (15000), `payment_success` (50000). Доскролл секций: `headline_*` / `section_*`. Дашборд: `https://<railway>/admin/behavior` · JSON `/api/behavior/stats`. Сайт: https://life-energy-phi.vercel.app/ · UTM — в `website/README.md`. Деплой API: [RAILWAY.md](./RAILWAY.md).
