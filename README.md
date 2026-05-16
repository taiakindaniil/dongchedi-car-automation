# avto-bot — Telegram-дайджест б/у авто с dongchedi.com

Сервис на Python, который каждый день парсит подержанные авто с
[dongchedi.com](https://www.dongchedi.com/usedcar) по гибким фильтрам из YAML,
ранжирует объявления взвешенным скорингом (свежесть, цена, пробег, владельцы,
отчёт инспекции, возраст, премиум-бренд), хранит историю в SQLite и
через [aiogram](https://docs.aiogram.dev/) шлёт топ-N новых объявлений в
Telegram.

## Что внутри

- `src/avto_bot/parsers/dongchedi/url_builder.py` — собирает 28-позиционный slug
  `/usedcar/...` из конфига (все фильтры сайта поддерживаются).
- `src/avto_bot/parsers/dongchedi/parser.py` — только **Chromium из Playwright** (не системный
  Chrome): `launch_persistent_context` с каталогом `PLAYWRIGHT_USER_DATA_DIR`
  (по умолчанию `./data/playwright_profile`), чтобы **cookies и сессия**
  сохранялись между запусками и уходили вместе с запросами к API. Перехват
  JSON: `/motor/pc/sh/sh_sku_list` (`search_sh_sku_info_list`) и
  `/motor/sh_go/sh_sku/list/`, плюс `playwright-stealth` на контексте и
  резерв `__NEXT_DATA__`. Цена в JSON часто в **шрифтовой обфускации**; после
  отрисовки страницы читаем реальную «3.88万» из **DOM** (`innerText`) и
  подставляем в `price_yuan`.
- `src/avto_bot/scorer.py` — взвешенная сумма семи под-скоров с
  расчётом «рыночной медианы» прямо по текущей выборке.
- `src/avto_bot/storage.py` — `aiosqlite`-хранилище для дедупа и
  отметок «новый сегодня / уже отправлено».
- `src/avto_bot/integrations/notifications/telegram.py` — `aiogram 3.x`, дайджест и карточки с
  фото, мягкий rate-limit.
- `src/avto_bot/main.py` — `APScheduler` + CLI `run` / `serve`.

## Быстрый старт (локально на Mac)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium

cp .env.example .env
# открой .env и подставь BOT_TOKEN и CHAT_ID

# при необходимости подправь config/filters.yaml
python -m avto_bot run   # разовый прогон
python -m avto_bot serve # демон по расписанию
```

## Запуск в Docker

```bash
cp .env.example .env
# подставь BOT_TOKEN и CHAT_ID

docker compose up -d --build
docker compose logs -f bot
```

Том `./data` хранит SQLite — историю объявлений между перезапусками, и
профиль Chromium (`./data/playwright_profile` по умолчанию) — cookies для
dongchedi. Папку профиля не коммитьте.
Том `./config` хранит фильтры — правь YAML на хосте, контейнер
подхватит при следующем тике.

## Где взять `BOT_TOKEN` и `CHAT_ID`

1. Создай бота у [@BotFather](https://t.me/BotFather) → `/newbot` → получи
   токен.
2. Добавь бота в нужный чат/канал и сделай его админом (если канал).
3. Узнай `chat_id`:
   - для лички: напиши боту «hi», открой
     `https://api.telegram.org/bot<TOKEN>/getUpdates`, возьми `chat.id`.
   - для канала: тот же URL, после публикации поста бот увидит апдейт.

## Конфигурация (`config/filters.yaml`)

Все поля опциональны. Пустое (или `null`) поле означает «не
фильтровать». Несколько значений — список.

```yaml
filters:
  city: 110000              # GB-код города, null = вся страна
  brand_ids: []             # [4, 3, 63] = BMW + Mercedes + Tesla
  body_family: []           # sedan | suv | mpv | sport
  body_class: []            # compact_suv | mid_suv | large_suv | ...
  price_wan: [null, 50]     # [from, to] в 万 RMB. 50 万 = 500 000 ¥
  year_range: [2018, null]
  km_max_wan: 10            # до 10 万 км пробега
  fuel: []                  # petrol | diesel | hev | bev | ext_range | phev | mild_hybrid
  transmission: null        # manual | auto
  drive: null               # fwd | rwd | awd
  emission: null            # guo4 | guo5 | guo6
  origin: null              # jv | domestic | jv_domestic | import
  inspected_only: false
  pages_to_scan: 3
  list_sort: newly_published_first   # или site_default

scoring_weights:
  freshness:   0.30
  price_value: 0.20
  low_km:      0.15
  owners:      0.10
  inspection:  0.10
  age:         0.10
  premium:     0.05

notify:
  top_n_per_day: 10
  schedule_cron: "0 10 * * *"   # 10:00 каждый день
  min_score: 0.45
  send_photos: true
  show_score_breakdown: false   # true — в карточке показать подскоры и веса
```

## Формула скоринга

Для каждого объявления считаем 7 нормализованных под-скоров в `[0, 1]`
и складываем их с весами из YAML:

```
score = w.freshness   * freshness
      + w.price_value * price_value
      + w.low_km      * low_km
      + w.owners      * owners
      + w.inspection  * inspection
      + w.age         * age
      + w.premium     * premium
```

Где:

- `freshness` — 1.0 если объявление впервые увидели сегодня,
  0.5 — если вчера, 0.0 — старее. Это и есть «гарантия свежести
  каждый день».
- `price_value` — `max(0, (median − price) / median)` среди peer-ов
  (тех же серий / моделей) из текущей выборки.
- `low_km` — `1 − clip(km_per_year / 20000, 0, 1)`.
- `owners` — `{0: 1.0, 1: 0.7, 2: 0.4, ≥3: 0.0}`.
- `inspection` — 1, если есть 检测报告.
- `age` — `1 − clip((current_year − model_year) / 10, 0, 1)`.
- `premium` — 1, если бренд в списке премиум (BMW / Mercedes / Audi /
  Porsche / Tesla / Lexus и т.п.).

Из выдачи отбираем те, у кого `notified_at IS NULL` и `score ≥
min_score`, сортируем и шлём топ-N.

## launchd-плистом (опционально, для запуска без Docker)

Создай `~/Library/LaunchAgents/com.avto-bot.daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.avto-bot.daily</string>
  <key>WorkingDirectory</key> <string>/Users/you/Desktop/avto-mvp-automation</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/Desktop/avto-mvp-automation/.venv/bin/python</string>
    <string>-m</string>
    <string>avto_bot</string>
    <string>run</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key>  <string>/tmp/avto-bot.out.log</string>
  <key>StandardErrorPath</key><string>/tmp/avto-bot.err.log</string>
</dict>
</plist>
```

Загрузить: `launchctl load ~/Library/LaunchAgents/com.avto-bot.daily.plist`.

## Если dongchedi не отдаёт страницу

Сайт обычно открыт из РФ, но при усиленной защите можно добавить
прокси:

```env
HTTP_PROXY=http://login:pass@proxy.example.com:3128
```

Парсер использует stealth-патч и реальный User-Agent.
