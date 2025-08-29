# Backend (FastAPI)

Backend реализует REST API для загрузки данных Slack (файл/вебхук), healthcheck и взаимодействия с базой данных.

## Структура
- `app/` — исходный код FastAPI приложения
  - `main.py` — основной файл приложения, реализует /healthcheck
  - `api/` — роуты FastAPI (upload, export, webhook)
- `tests/` — тесты
- `Dockerfile` — контейнеризация
- `requirements.txt` — зависимости

## Cursor instructions
- В директории backend всегда должен быть актуальный README.md с описанием структуры и назначения.
- При добавлении новых файлов/директорий — обновлять описание.
- Все соглашения и инструкции фиксируются в этом README.md.
- Пример: если добавлен новый роут или модуль, добавить его описание в раздел "Структура".

## Соглашения
- Используется FastAPI, структура — по best practices.
- Все изменения отражаются в этом README.
- Эндпоинт `/healthcheck` возвращает статус backend для фронта.
- Эндпоинт `POST /export` запускает экспорт данных в Mattermost в фоновом режиме.
- **Все новые обработчики и тяжёлые операции должны быть реализованы через async def.**
- **Upload отдаёт ответ об успехе/неуспехе сразу после завершения загрузки файла, до парсинга.**
- **Во всех частях backend обязательно использовать логгирование. Уровень логирования выбирается по задаче: например, если при создании пользователя Mattermost возвращает ошибку "уже существует", это DEBUG, а не ERROR.** 

## Логгирование (dev)
- Конфигурация: `app/logging_config.py` (root + логгеры uvicorn/httpx + `backend_logger`).
- В dev-окружении включены access-логи Uvicorn и `UVICORN_LOG_LEVEL=INFO` (см. `infra/docker-compose.dev.yml`).
- Смотреть логи: `docker compose -f infra/docker-compose.dev.yml logs -f backend`.

## Поиск по username и функциональные индексы

- Для быстрого поиска пользователей по username (и других сущностей по вложенным полям) используется функциональный индекс по выражению `raw_data->>'username'` для пользователей (entity_type = 'user').
- Индекс создаётся миграцией Alembic `002_add_username_index`.
- Пример SQL-запроса:
  ```sql
  SELECT * FROM entities WHERE entity_type = 'user' AND raw_data->>'username' = 'vasya';
  ```
- Для поиска через SQLAlchemy используйте:
  ```python
  stmt = select(Entity).where(
      Entity.entity_type == 'user',
      Entity.raw_data['username'].astext == 'vasya'
  )
  result = await session.execute(stmt)
  user_mapping = result.scalar_one_or_none()
  ```

## Alembic: универсальный запуск миграций

- Путь к миграциям теперь относительный: `script_location = backend/alembic` в alembic.ini.
- Миграции можно запускать как нативно, так и в Docker:
  - **Нативно:**
    ```bash
    alembic -c alembic.ini upgrade head
    ```
    (из корня проекта)
  - **В Docker:** путь к alembic.ini и миграциям также будет корректен, если /app — это корень проекта. 

## Экспорт данных в Mattermost

### Архитектура
- Экспорт реализован через оркестратор (services/export/orchestrator.py), который обрабатывает сущности по очереди: user, custom_emoji, attachment, channel, message, reaction.
- Для каждого типа сущности используется отдельный экспортер (например, UserExporter), реализующий бизнес-логику экспорта.
- HTTP-запросы к Mattermost вынесены в MMApiMixin (services/export/mm_api_mixin.py), что позволяет легко переключаться между штатным API и плагином.

### Управление статусами
- Все экспортеры наследуют `ExporterBase` с методом `set_status(status, error=None)`
- Статусы обновляются в БД через SQL UPDATE для корректного отслеживания прогресса
- Поддерживаемые статусы: `pending`, `success`, `failed`, `skipped`
- При ошибке сохраняется `error_message` для диагностики

### Экспорт пользователей
- Все поля для Mattermost заполняются по максимуму из raw_data Slack.
- Пароль всегда пустой, auth_service = "gitlab", auth_data вычисляется по кастомному хэшу от username.
- Если Mattermost возвращает ошибку email_exists или username_exists, экспортер повторно запрашивает пользователя по email/username, записывает его id в mapping и считает экспорт успешным.
- Любая другая ошибка фиксируется в поле error_message, статус становится failed.

### Экспорт кастомных эмодзи
- Требует `mm_user_id` (ID пользователя-владельца токена) для создания эмодзи
- Использует multipart/form-data с полями `image` (файл) и `emoji` (JSON метаданные)
- Поддерживает только PNG, JPEG, GIF форматы
- Имена эмодзи должны быть 1-64 символа, только строчные буквы и цифры
- URL эмодзи из Slack могут требовать аутентификации

### Переменные окружения
- MM_URL — адрес Mattermost (например, http://mattermost:8065)
- MM_TOKEN — токен администратора Mattermost
- EXPORT_WORKERS — количество параллельных воркеров экспорта (по умолчанию 5)

#### Производительность / Тюнинг
- ATTACHMENT_WORKERS — воркеры загрузки файлов (по умолчанию = EXPORT_WORKERS)
- EXPORT_CHANNEL_CONCURRENCY — параллельных каналов для экспорта сообщений (по умолчанию = EXPORT_WORKERS)
- MM_MAX_KEEPALIVE, MM_MAX_CONNECTIONS, MM_HTTP2 — настройки HTTP пула клиентов
- DB_POOL_SIZE, DB_MAX_OVERFLOW, DB_POOL_TIMEOUT — настройки пула подключений к БД

### Логирование
- Все логи экспорта и ошибок централизованы через backend_logger.
- Вся информация об ошибках экспорта пишется в поле error_message таблицы entities.
- Одно действие экспорта = одна строка INFO лога, остальные логи DEBUG.

### Особенности реализации
- Оркестратор получает `mm_user_id` один раз в начале экспорта
- Параметр `mm_user_id` передается только в экспортеры, которые его требуют (например, CustomEmojiExporter)
- Статусы обновляются асинхронно и не блокируют основной поток экспорта

### Расширение
- Для других сущностей (каналы, сообщения, реакции и т.д.) архитектура аналогична: реализуется экспортер, добавляется from_entity, используется MMApiMixin.
- Если экспортер требует дополнительные параметры (как `mm_user_id` для эмодзи), они передаются через конструктор. 