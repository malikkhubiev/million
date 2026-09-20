# Что настроить в Яндекс.Метрике и Яндекс.Директе

Сайт: https://life-energy-phi.vercel.app/  
Бот / API / дашборд: https://million-zcqy.onrender.com/admin/behavior  

**Где смотреть поведение пользователей:** свой дашборд (avg / median / min / max, фильтры, группы, выгрузка `.txt`).  
**Зачем Метрика и Директ:** цели для оптимизации рекламы, автопометка `yclid`, отчёты по UTM в интерфейсе Яндекса.

---

## 1. Яндекс.Метрика (счётчик `112323537`)

### 1.1. Базовые настройки счётчика

1. Метрика → счётчик → **Настройки**.
2. Включи **Вебвизор**, **Карту кликов**, **Электронную коммерцию**.
3. **Безопасность** → включи **Measurement Protocol** → скопируй токен.
4. В Render (сервис `million`) добавь переменную:
   - `METRIKA_MP_TOKEN` = этот токен  
   Без неё цели из бота (`bot_started`, `show_phone`, `payment_*`) не доедут.

### 1.2. Цели (тип: JavaScript-событие)

Создай цели с **идентификатором** ровно как в таблице (поле «Идентификатор цели»).

#### Воронка для Директа

| Идентификатор | Что значит | Ценность (рекоменд.) |
| --- | --- | --- |
| `view_offer` | Доскролл до блока оплаты | 50 ₽ |
| `tg_click` | Клик «в Telegram» на сайте | 30 ₽ |
| `bot_started` | Человек нажал /start в боте | 150 ₽ |
| `show_phone` | Нажал «Показать номер» в боте | 1 000 ₽ |
| `payment_started` | Нажал «Оплатить» в боте | 15 000 ₽ |
| `payment_success` | Успешная оплата ЮKassa | 50 000 ₽ |

Оптимизация кампании: в итоге на `payment_success`. Пока мало оплат — временно `payment_started` или `show_phone`.

#### Доскролл секций (для воронки в Метрике)

Можно создать и `headline_*`, и `section_*` (сайт шлёт оба).

| Цель | Секция |
| --- | --- |
| `headline_1` / `section_top` | Герой |
| `headline_2` / `section_one` | Тебе не нужно становиться другой |
| `headline_3` / `section_feel` | Тобой управляет состояние |
| `headline_4` / `section_recognize` | Ты узнаешь себя? |
| `headline_5` / `section_manifesto` | Когда ты управляешь состоянием |
| `headline_6` / `section_method` | Управление состоянием |
| `headline_7` / `section_author` | Автор |
| `headline_11` / `section_results` | До и после |
| `headline_9` / `section_inside` | Формат |
| `headline_12` / `section_not_for` | Кому не подходит |
| `headline_10` / `section_purchase` | Оплата |

### 1.3. Сегменты «хороший / плохой» в Метрике

1. Сегмент **Хорошие**: достигли цели `bot_started`.
2. Сегмент **Плохие**: не достигли `bot_started`.
3. Сравни конверсии `view_offer` / `tg_click` и параметры визита в двух сегментах.

Точные avg / median / min / max по времени на секциях смотри в дашборде, не в Метрике.

### 1.4. UTM в Метрике

Метрика сама читает UTM из URL. В отчётах:

- **Источники → Метки UTM**
- Группировки: `utm_campaign`, `utm_content` (объявление), `utm_term` (ключ)

---

## 2. Яндекс.Директ — UTM на 9 объявлений

### 2.1. Общий шаблон ссылки

```
https://life-energy-phi.vercel.app/?utm_source=yandex&utm_medium=cpc&utm_campaign=CAMPAIGN&utm_content=AD&utm_term={keyword}
```

`{keyword}` — подстановка Директа (не заменяй вручную).

В объявлении: **Параметры URL** → вставь строку меток из таблицы.

### 2.2. Группа «Проблемы» → `utm_campaign=problems`

| Объявление | utm_content | Строка меток |
| --- | --- | --- |
| Почему жизнь больше не радует? | `prob_life_no_joy` | `utm_source=yandex&utm_medium=cpc&utm_campaign=problems&utm_content=prob_life_no_joy&utm_term={keyword}` |
| Куда исчезла твоя энергия? | `prob_energy_gone` | `utm_source=yandex&utm_medium=cpc&utm_campaign=problems&utm_content=prob_energy_gone&utm_term={keyword}` |
| Почему ты постоянно на пределе? | `prob_on_edge` | `utm_source=yandex&utm_medium=cpc&utm_campaign=problems&utm_content=prob_on_edge&utm_term={keyword}` |

### 2.3. Группа «Решения» → `utm_campaign=solutions`

| Объявление | utm_content | Строка меток |
| --- | --- | --- |
| Практики для энергии и спокойствия | `sol_practices` | `utm_source=yandex&utm_medium=cpc&utm_campaign=solutions&utm_content=sol_practices&utm_term={keyword}` |
| Пойми, как устроено твоё состояние | `sol_understand` | `utm_source=yandex&utm_medium=cpc&utm_campaign=solutions&utm_content=sol_understand&utm_term={keyword}` |
| Трансформация начинается с состояния | `sol_starts_state` | `utm_source=yandex&utm_medium=cpc&utm_campaign=solutions&utm_content=sol_starts_state&utm_term={keyword}` |

### 2.4. Группа «Премиум-lifestyle» → `utm_campaign=premium`

| Объявление | utm_content | Строка меток |
| --- | --- | --- |
| Красивой жизни мало. Важно её чувствовать | `prem_feel_life` | `utm_source=yandex&utm_medium=cpc&utm_campaign=premium&utm_content=prem_feel_life&utm_term={keyword}` |
| Твоя жизнь принадлежит тебе | `prem_belongs_you` | `utm_source=yandex&utm_medium=cpc&utm_campaign=premium&utm_content=prem_belongs_you&utm_term={keyword}` |
| Высокий уровень жизни начинается внутри | `prem_inside` | `utm_source=yandex&utm_medium=cpc&utm_campaign=premium&utm_content=prem_inside&utm_term={keyword}` |

### 2.5. Чеклист в Директе

1. У каждого из 9 объявлений — своя строка UTM (разный `utm_content`).
2. В стратегиях укажи цели Метрики из таблицы воронки.
3. Включи автопометку Яндекс.Директа (`yclid`) — она уже пишется в behavior и в клиента бота.
4. В отчётах Директа/Метрики смотри разрез по `utm_content` = конкретное объявление.

---

## 3. Свой дашборд (основной контроль)

Открой: https://million-zcqy.onrender.com/admin/behavior

Там можно:

- фильтровать по датам, UTM, bot_started / click, секции, yclid, поиску;
- группировать по объявлению / кампании / дню / telegram-флагам;
- сортировать визиты и колонки секций;
- видеть **avg / median / min / max** для time_to и dwell по каждой секции;
- сравнить «хороших» и «плохих»;
- **Скачать .txt** — полный дамп с фильтрами, группами, всеми визитами, датами (`created_at` / `updated_at`) и секциями.

Прямая выгрузка:  
https://million-zcqy.onrender.com/api/behavior/export.txt  

JSON с теми же фильтрами:  
https://million-zcqy.onrender.com/api/behavior/stats  

---

## 4. Быстрая проверка, что всё живое

1. Открой сайт с тестовыми метками, например:  
   `https://life-energy-phi.vercel.app/?utm_source=yandex&utm_medium=cpc&utm_campaign=problems&utm_content=prob_life_no_joy&utm_term=тест`
2. Поскролль несколько секций, нажми кнопку в Telegram, сделай `/start` в боте.
3. Обнови дашборд — должен появиться визит с UTM, доскроллами и флагом `bot_started`.
4. В Метрике (через 15–30 мин) проверь срабатывание целей.
