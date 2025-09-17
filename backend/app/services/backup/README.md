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