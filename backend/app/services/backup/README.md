# backup/

Подсистема работы с исходным Slack экспортом:
- `zip_utils.py` — распаковка архивов.
- `file_storage.py` — временное и постоянное хранение загруженных архивов.
- `messages_import.py` — единый высокопроизводительный импорт сообщений, реакций, вложений и кандидатов кастомных эмодзи.
- `orchestrator.py` — координация полного импорта (users, channels, unified messages+related).
- `progress_tracker.py` — унифицированные parsed/processed счётчики.

Специализированные частичные импортёры (attachments, reactions, custom emojis) удалены — всё консолидировано в единый поток `parse_messages_and_related`.

## Упрощённый импорт

Последовательность:
1. Распаковка архива в рабочую директорию.
2. Чтение структуры каналов (карта папка→channel).
3. Однопроходный парсинг всех JSON файлов каналов.
4. Пакетные вставки entities + пакетные вставки связей.
5. Обновление прогресса через `ProgressTracker`.

## Пример (высокоуровневый вызов)
```python
from app.services.backup.orchestrator import import_slack_backup
await import_slack_backup(zip_path, job_id=job_id)
```

## Архивы
- Для корректной работы с кириллическими именами архива Slack рекомендуется утилита `unzip -O UTF-8` (реализовано в `zip_utils.py`).

## Инварианты
- Каждое импортированное сообщение имеет `posted_in` отношение (канал) — обеспечивается в момент вставки.

## Производительность
- Используются настраиваемые размеры батчей для сообщений/реакций/вложений и связей.
- Конкурентность по каналам регулируется `IMPORT_CHANNEL_CONCURRENCY`.

## Прогресс
- `*_parsed` и `*_processed` хранятся в JSONB `import_jobs.meta` с единым интервалом флеша (`IMPORT_PROGRESS_FLUSH_INTERVAL_SEC`).