# db/

Миграции и инициализация базы данных для Slack-MM2 Sync.

## Структура
- `migrations/` — SQL-миграции для создания и изменения структуры БД
  - `001_init_universal_schema.sql` — первая миграция: универсальная схема для хранения сущностей Slack и связей между ними

## Универсальная схема
- Все сущности (user, channel, message, file, emoji и др.) хранятся в таблице `entities` с полями:
  - `entity_type` — тип сущности
  - `slack_id` — идентификатор в Slack
  - `mattermost_id` — идентификатор в Mattermost (может быть NULL)
  - `raw_data` — сырые данные из Slack (JSONB)
  - `status`, `error_message` — статус миграции и ошибки
- Все связи между сущностями (например, user состоит в channel, message принадлежит channel, file прикреплён к message) хранятся в таблице `entity_relations` с типом связи и ссылками на сущности.

## Применение миграций

Пример для psql:
```bash
psql $DATABASE_URL -f migrations/001_init_universal_schema.sql
```

## Cursor instructions
- При добавлении новых миграций или изменении схемы — обновлять этот README.md.
- Все соглашения и структура фиксируются здесь. 