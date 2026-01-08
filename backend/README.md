# Backend (FastAPI)

Backend реализует REST API для загрузки данных Slack (файл/вебхук), healthcheck и взаимодействия с базой данных.

**Для разработчиков**: См. [Developer Documentation](../docs/dev.md) для полной информации по настройке среды разработки, тестированию и workflow.

## Import / Export Pipeline

The importer runs in a single pass reading Slack export JSON files, creating entities (users, channels, messages, reactions, attachments, custom emoji) and their relations. The exporter then processes entities in dependency order.

### Counter Semantics (Ingestion vs Export)

To avoid misleading progress signals we distinguish two classes of counters recorded in `ImportJob.meta`:

* `<type>_processed` — Monotonic ingestion counters. Incremented only when an entity of that type is successfully parsed & persisted (with required base invariants) during the import phase. These DO NOT decrease or reset when export begins.
* `<type>_exported` — Dynamic export counters. Reflect how many entities of a type have transitioned out of `pending` (sum of success + skipped + failed). These can lag `processed` while entities wait in the export queue and advance as exporter workers complete.

`meta.totals` is frozen after import stages complete (entering `exporting` or `done`) to keep denominators stable (prevents >100% progress scenarios). Frontend export progress bars should use the exported counters against the frozen totals. Ingestion progress displays (during import stages) can still reference processed counters for a monotonically increasing view of parsing throughput.

### Reaction Integrity Guarantees

Reactions require two relations to be considered fully ingested and eligible for successful export:

1. `reacted_by`   (user -> reaction)
2. `reacted_to`   (reaction -> message)

The importer now:
* Persists each reaction entity.
* Attempts to create both relations with granular exception logging (no broad silent swallow).
* Verifies both relations exist; reactions missing either relation are left out of the `reactions_processed` count (they remain `pending`).
* A post-pass integrity check logs aggregate counts of reactions missing either relation (`[INTEGRITY][reaction] ...`).

Exporter guards still protect Mattermost API calls; such incomplete reactions will be marked skipped with a clear reason if they reach export unchanged. A future repair utility can rebuild missing relations and reset affected reactions back to `pending` for a clean re-export.

### Emoji Detection
Emoji references detected in message text (using a lightweight `:name:` regex) are persisted as `custom_emoji` entities (if present in the provided emoji list) for later export sequencing before messages.
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

## Alembic: миграции (схлопнутая история)

Как применить на чистой базе:
```bash
alembic -c alembic.ini upgrade head
```

Обновление существующей инсталляции с историческими данными не поддерживается автоматически — требуется либо:
1. Создать новую БД и выполнить `upgrade head`, затем повторно импортировать Slack архив; или
2. Вручную привести схему к финальному состоянию (смотри содержимое `001_initial_full_schema`) и выполнить `alembic stamp 001_initial_full_schema`.

Ключевые особенности текущей схемы:
* Глобальная уникальность сущностей: уникальный индекс `uq_entities_type_slack` (без job_id).
* Таблица `import_jobs` с JSONB полем `meta` для прогресса.
* Таблица `entity_relations` с уникальностью `(from_entity_id, to_entity_id, relation_type)` и производительными индексами для `relation_type` + направления.
* Функциональные / частичные индексы для username и reaction lookup.
* Производительные индексы по `(job_id, entity_type, status)` и отдельный `job_id` для статистики.

Если потребуется снова вести историю инкрементальных миграций, создавайте новые файлы с `down_revision = '001_initial_full_schema'`.

## Экспорт данных в Mattermost

Полная детализация вынесена в `app/services/export/README.md` (единственный источник правды по порядку типов и правилам). Ниже сводка. Стартовое автоматическое включение/установка плагина УДАЛЕНО; управление теперь строго через API (см. раздел "Управление плагином").

### Архитектура (сводка)
- Оркестратор (`services/export/orchestrator.py`) обрабатывает сущности с глобальным барьером типо́в в порядке:
  `user → custom_emoji → channel → attachment → message → reaction`.
  (Ранее в документации ошибочно фигурировал порядок с attachment перед channel — исправлено.)
- Для каждого типа сущности — свой экспортер (UserExporter, ChannelExporter, AttachmentExporter, MessageExporter, ReactionExporter и т.д.).
- HTTP-взаимодействие с Mattermost (ядро + плагин) инкапсулировано в `MMApiMixin`.

### Управление статусами
- Все экспортеры наследуют `ExporterBase` с методом `set_status(status, error=None)`.
- Статусы обновляются через `UPDATE` (не оставляя «подвешенных» pending на ошибках).
- Поддерживаемые статусы: `pending`, `success`, `failed`, `skipped`.
- При ошибке текст фиксируется в `error_message`.

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

### Переменные окружения (экспорт)
- `MM_URL` — базовый URL Mattermost (например, http://mattermost:8065)
- `MM_TOKEN` — админский (или системный) токен
- `EXPORT_WORKERS` — число воркеров для глобальных и job-scoped типов
- `SKIP_ATTACHMENT_EXPORT` — если выставлен в `1`/`true`, этап экспорта вложений пропускается (все pending аттачменты немедленно переходят в `skipped`). Используйте только как аварийный рубильник и возвращайте статус вручную, если нужно переэкспортировать файлы после отключения.

#### Производительность / Тюнинг
- `ATTACHMENT_WORKERS` — воркеры загрузки файлов (если не задан, используется значение `EXPORT_WORKERS`).
- `EXPORT_CHANNEL_CONCURRENCY` — максимальное число каналов, экспортируемых параллельно при публикации сообщений (по умолчанию равно `EXPORT_WORKERS`, либо может переопределяться).
- `MM_MAX_KEEPALIVE`, `MM_MAX_CONNECTIONS`, `MM_HTTP2` — параметры HTTP пула.
- `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT` — параметры пула соединений БД.
- `ATTACHMENT_URL_TIMEOUT_SECONDS` — расширенный таймаут (секунды) для долгих скачиваний больших вложений через эндпоинт плагина `/attachment_from_url` (по умолчанию 600). Значение применяется только к этому эндпоинту: увеличивает `read`/`write` таймауты httpx при потоковой передаче. Уменьшите значение для более агрессивного обнаружения зависаний, увеличьте — для видео/архивов большого размера.

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

## Импорт Slack (текущая упрощённая модель)

Импорт теперь всегда идёт по единому single-pass сценарию:
```
extracting → users → channels → messages (включая reactions, attachments, custom emojis) → exporting → done
```
Отдельных стадий `reactions`, `attachments`, `emojis` больше нет — всё создаётся внутри прохода сообщений.

### Атомарные обновления метаданных / счётчиков
Все прогресс-счётчики (`*_processed`) и сервисные поля в `ImportJob.meta` обновляются через единый SQL builder `merge_job_meta` (атомарный UPDATE JSONB). Это решает историческую проблему lost update при смешении ORM read-modify-write и отдельных UPDATE выражений.

Поддерживаемые операции:
* incr — атомарное увеличение числовых значений
* max — монотонный максимум (страховка при конкурентных инкрементах)
* set — установка точного значения (стадия, служебные флаги)
* nested — слияние вложенных объектов (`totals`, `durations_ms`)
* remove — удаление ключей (используется редко)

Технические детали:
* Все параметры сериализуются в ::jsonb, числовые инкременты приводятся к ::int.
* Отсутствует промежуточное чтение meta перед записью.
* Один SQL round‑trip на группу операций.
* Исключён `IndeterminateDatatypeError` (явные касты типов).

Инварианты детерминированности:
1. Ни один *_processed не уменьшается.
2. При входе в `exporting` выполняется консолидация финальных значений в `meta.totals`.
3. Эндпоинт `/jobs` для стадий `exporting`/`done` возвращает максимум из live меты и totals.
4. Мини-интеграционный сценарий видит финальные значения уже на раннем POLL (обычно 2-й) — это «ранний успех».
 5. Флаг `totals_frozen=true` появляется только после консолидации и сигнализирует фронту, что denominator стабильный.

Если добавляете новый счётчик:
1. Обновите логику инкремента в месте импорта.
2. Добавьте поле в таблицу README (root и этот файл).
3. Обновите mini-интеграционный скрипт с новым ожидаемым значением.

### Ранний успех мини-набора
CI / локальный скрипт прекращает опрос, когда стадия = `exporting` и все ожидаемые финальные счётчики совпали. Переход в `done` не обязателен для теста на корректность данных.

### Формат Slack архива (жёсткое требование)
Импорт поддерживает только «плоский» формат: JSON-файлы верхнего уровня (`users.json`, `channels.json`, `groups.json`, `dms.json`, опц. `mpims.json`) и директории каналов/DM лежат непосредственно в корне архива zip. Любая дополнительная обёрточная папка (nested root) приводит к немедленной ошибке в стадии `extracting`.



#### PLUGIN_BUNDLE_URL

Опциональный URL для удалённого артефакта `mm-importer-<version>.tar.gz`, если локальный недоступен. Backend:
* Проверяет доступность (HEAD/GET) при вызове `/plugin/ensure` или `/plugin/status`.
* Скачивает во временную директорию и перемещает в каталоги bundle (ro-mount) атомарно.
* Не делает этого на старте приложения (никаких side-effects в lifespan).
* Возвращает `remote_url` в статусе при наличии.

Ошибки скачивания -> `warning` лог + статус `needs_bundle` (без 5xx). Это конфигурация источника артефакта, а не feature-флаг.

##### Управление плагином
| Эндпоинт | Действие |
|----------|----------|
| `GET /plugin/status` | Текущий статус (enabled, локальная/удалённая доступность bundle, версия) |
| `POST /plugin/ensure` | Убедиться, что bundle загружен и установлен (скачать при необходимости) |
| `POST /plugin/deploy` | Загрузить новый bundle (без включения) |
| `POST /plugin/enable` | Включить уже установленный bundle |
| `POST /plugin/reinstall` | Принудительная переустановка (перезагрузка плагина) |

Архитектурно backend не монтирует исходники плагина для записи — используется выделенный read-only каталог bundles. Статическая сборка (CGO_DISABLED=0) обязательна для запуска в минимальном container образе Mattermost.

### Что делать при обновлении инсталляции
1. Обновить код до текущей версии.
2. Удостовериться, что сторонние скрипты / мониторинг не зависят от parsed_* ключей в `job.meta`.
3. Проверить UI: прогресс теперь показывает только `messages_processed` и агрегированные счётчики для reactions / attachments / emojis.
4. Выполнить миграцию Alembic до head, чтобы применить глобальный уникальный индекс. Если существовали дубли `(entity_type, slack_id)` — останется запись с максимальным id.

### Семантика повторного импорта (idempotency)
При повторном запуске импорта того же архива или расширенного набора данных для уже экспортированных сущностей:
* `success` остаётся неизменным — сущность не переэкспортируется.
* `failed` / `skipped` / `pending` получают шанс на повторную обработку: сырой payload обновляется, статус сбрасывается в `pending`.
* Экспортные воркеры видят единственную запись и обновляют её статус глобально.
