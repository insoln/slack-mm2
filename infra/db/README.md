# db/

Миграции и инициализация базы данных для Slack-MM2 Sync.

## Структура
- `migrations/` — SQL-миграции для нашей универсальной схемы хранения сущностей
  - `001_init_universal_schema.sql` — базовая схема: `entities`, `entity_relations`
- `init-mattermost.sql` — dev-инициализация схемы Mattermost, применяемая контейнером Postgres в docker-compose.dev

## Универсальная схема
- Все сущности (user, channel, message, file, emoji и др.) хранятся в таблице `entities` с полями:
  - `entity_type` — тип сущности
  - `slack_id` — идентификатор в Slack
  - `mattermost_id` — идентификатор в Mattermost (может быть NULL)
  - `raw_data` — сырые данные из Slack (JSONB)
  - `status`, `error_message` — статус миграции и ошибки
- Все связи между сущностями (например, user состоит в channel, message принадлежит channel, file прикреплён к message) хранятся в таблице `entity_relations` с типом связи и ссылками на сущности.

## Mattermost dev-схема и ограничения
- В `init-mattermost.sql` восстановлены необходимые enum-тип и ключевые таблицы Mattermost для работы в dev-среде.
- Для таблицы каналов включено ограничение уникальности: `UNIQUE(teamid, name)` — это согласовано с поведением плагина при создании/получении каналов по нормализованному имени.
- Скрипт настроен идемпотентно: повторное применение не ломает данные.

## Пересборка базы в docker-compose.dev
- Контейнер `db` монтирует `infra/db/init-mattermost.sql` в `/docker-entrypoint-initdb.d/` и применяет при первом старте тома.
- Чтобы полностью пересоздать БД с нуля:
  1) Остановить окружение и удалить тома Postgres.
  2) Запустить docker compose снова, чтобы скрипт инициализации применился заново.

Пример (осторожно: удаляет данные):

```bash
# остановить окружение
docker compose -f infra/docker-compose.dev.yml down -v
# поднять заново
docker compose -f infra/docker-compose.dev.yml up -d
```

## Применение миграций универсальной схемы
Пример для psql:

```bash
psql $DATABASE_URL -f infra/db/migrations/001_init_universal_schema.sql
```

Обновляйте этот README при изменении схемы или ограничений.