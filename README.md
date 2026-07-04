# Game Product Analytics

MVP-платформа для продуктовой аналитики игр из Steam.

Сейчас умеет:

- искать игру в Steam по названию;
- выгружать Steam-отзывы по `app_id`, датам и языку;
- прогонять отзывы через LLM для продуктового анализа;
- работать без LLM-ключа через простой fallback-анализ по тональности и ключевым словам;
- запускаться как HTTP API или CLI-скрипт.

## Быстрый старт

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

LLM необязателен. Если нужен глубокий разбор отзывов, заполни `.env`:

```bash
LLM_API_KEY=...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

Подойдет любой OpenAI-compatible endpoint.

## Запуск API

```bash
uvicorn game_product_analytics.main:app --reload
```

Проверка:

```bash
curl http://127.0.0.1:8000/health
```

Поиск игры:

```bash
curl 'http://127.0.0.1:8000/steam/search?query=Dota%202&limit=5'
```

Анализ отзывов:

```bash
curl -X POST http://127.0.0.1:8000/analysis/reviews \
  -H 'Content-Type: application/json' \
  -d '{
    "game_query": "Dota 2",
    "start_date": "2026-06-01",
    "end_date": "2026-07-01",
    "max_reviews": 100,
    "language": "all"
  }'
```

Если `app_id` уже известен, можно не делать поиск:

```json
{
  "app_id": 570,
  "start_date": "2026-06-01",
  "end_date": "2026-07-01",
  "max_reviews": 100,
  "language": "all"
}
```

## Запуск как скрипт

```bash
python scripts/analyze_reviews.py "Dota 2" --start-date 2026-06-01 --end-date 2026-07-01 --max-reviews 100
```

Или через установленный entrypoint:

```bash
game-analytics "Dota 2" --start-date 2026-06-01 --end-date 2026-07-01 --max-reviews 100
```

## Что возвращает анализ

Ответ содержит:

- найденную игру и `app_id`;
- период выгрузки;
- количество найденных отзывов;
- sentiment breakdown по Steam thumbs up/down;
- краткое резюме;
- что игрокам нравится;
- основные боли;
- feature requests;
- упоминания монетизации;
- технические проблемы;
- заметные цитаты.

## Ограничения MVP

- Steam reviews API не является официально стабильным контрактом, поэтому поля могут меняться.
- Выгрузка по периоду реализована через постраничный `recent` cursor и фильтрацию timestamp на нашей стороне.
- Для больших периодов и популярных игр нужно добавить очередь задач и хранение сырых отзывов в БД.
- Fallback-анализ нужен только чтобы MVP работал без LLM; для реальной продуктовой аналитики лучше использовать LLM.

## Следующие шаги

- Добавить БД для игр, выгрузок и результатов анализа.
- Добавить фоновые jobs для долгих выгрузок.
- Сделать UI с поиском игры, выбором периода и отчетом.
- Добавить сравнение периодов: до/после патча, релиза DLC или изменения цены.
- Добавить кластеризацию тем отзывов и тренды по времени.
