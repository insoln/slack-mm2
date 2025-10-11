# backup/

Подсистема работы с исходным Slack экспортом:
- `zip_utils.py` — распаковка архивов.
- `file_storage.py` — временное и постоянное хранение загруженных архивов.
- `messages_import.py` — единый высокопроизводительный импорт сообщений, реакций, вложений и кандидатов кастомных эмодзи.
 - `orchestrator.py` — координация полного импорта (users, channels, messages+related, export).
 - (устар.) `attachments_import.py`, `reactions_import.py` — сохранены как заглушки/совместимость.
 - Прогресс‑трекирование через прямые обновления JSONB меты (без progress_tracker).

## Упрощённый импорт (single-pass)

Последовательность стадий:
`extracting → users → channels → messages (сообщения + reactions + attachments + custom emoji usage) → exporting → done`

Особенности:
* Нет отдельных стадий reactions / attachments / emojis.
* Счётчики прогресса: `messages_processed` + дельты для других сущностей.
* Итоговые totals агрегируются в конце стадии messages.
* Конкурентность по каналам управляется `IMPORT_CHANNEL_CONCURRENCY` (по умолчанию 1).

## Пример (высокоуровневый вызов)
```python
from app.services.backup.orchestrator import import_slack_backup
await import_slack_backup(zip_path, job_id=job_id)
```

## Архивы
- Для корректной работы с кириллическими именами архива Slack рекомендуется утилита `unzip -O UTF-8` (реализовано в `zip_utils.py`).

## Инварианты
- Каждое импортированное сообщение имеет `posted_in` отношение (канал) — обеспечивается в момент вставки.

## Прогресс
В `import_jobs.meta` обновляются ключи: `messages_processed`, `reactions_processed`, `attachments_processed`, `emojis_processed`, `json_files_processed`, `json_files_total`, `current_stage`, `stages`, `totals`, опционально `durations`.

## Тестовый минимальный архив
Для интеграционного/локального тестирования добавлен небольшой искусственный экспорт Slack:

`infra/test-data/slack-mini-backup.zip` (распакованная версия: `infra/test-data/slack-mini-backup/`)

Содержит:
1. Два пользователя (`U0001`, `U0002`).
2. Публичный канал (`public-channel` / `C0001`).
3. Приватный канал (`private-channel` / `G0001`).
4. Личный диалог (DM `D0001`).
5. По два дня активности (2025-01-01, 2025-01-02) в каждом канале/DM.
6. В каждый день по два сообщения: одно с вложением (чередуются три типа файлов — text/plain, image/png, application/zip), одно без.
7. Пример треда (reply через `thread_ts`).
8. Пример реакции (`thumbsup`) без использования кастомных эмодзи.
9. Сообщение бота (`subtype=bot_message`, `bot_id=B0001`).
10. Отредактированное сообщение (`edited`).
11. Тумбстоун удалённого сообщения (`subtype=message_deleted`, `hidden=true`).

Цели покрытия:
- Проверка отношений `posted_in`, `posted_by`, `thread_reply`, `reacted_by`, `reacted_to`, `attached_to`.
 - Дополнительно: покрытия веток для `bot_message`, обработка `edited`, игнор/тумбстоун `message_deleted`.
- Валидация пакетных вставок для сообщений/реакций/вложений на маленьком объёме.
- Отсутствие зависимостей от Slack API (нет кастомных emoji URL, только стандартные реакционные имена).

Пересборка zip без системной `zip` утилиты:
```
python infra/test-data/build_mini_backup_zip.py
```

Использование в тестах: укажите путь к zip при создании `ImportJob` или через API загрузки.

### Локальные тестовые вложения (service `test-files`)
В dev `docker-compose.dev.yml` добавлен сервис `test-files` (порт 9000), который отдаёт содержимое директории `infra/test-data/` через простой Python HTTP сервер. 

В тестовом архиве `url_private` указывает на ссылки вида:
```
http://test-files:9000/slack-mini-backup/files/example.txt
```
Импортер допускает несколько префиксов URL, задаваемых переменной окружения `IMPORT_URL_PREFIXES` (CSV). Значение по умолчанию:
```
IMPORT_URL_PREFIXES="https://files.slack.com,http://test-files:9000/"
```
Это позволяет без модификации кода принимать как реальные Slack ссылки, так и локальные тестовые.

Если нужно добавить ещё один источник (например `http://minio:9001/`), просто расширьте переменную:
```
IMPORT_URL_PREFIXES="https://files.slack.com,http://test-files:9000/,http://minio:9001/"
```
