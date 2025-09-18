# Infra

Инфраструктурные файлы для деплоя приложения в Kubernetes, настройки БД, Mattermost и CI/CD.

## Структура
- `k8s/` — манифесты Kubernetes
- `db/` — миграции и инициализация Postgres
- `plugin/` — Mattermost plugin (см. `plugin/README.md`)
- `mattermost/` — конфиг Mattermost (`mm_config.json`)
- `test-data/` — мини-набор Slack и вспомогательные файлы (используются импортом и сервисом test-files)
- `docker-compose.dev.yml` — dev-окружение: backend, frontend, Postgres, Mattermost, test-files
- `docker-compose.prod.yml` — prod-окружение: backend, frontend, persistent Postgres (без Mattermost)
- `docker-compose.yml` — базовый compose (может использоваться как prod)

## Окружения

### Development (dev)
Запускается полный стек сервисов (никаких частичных запусков): backend, frontend, Mattermost, Postgres, test-files.

- Backend → Mattermost: `MM_URL=http://mattermost:8065`.
- Токен (`MM_TOKEN=5x7rr788c7gwdnkdr9imb49ffo`) зашит init-м скриптом.
- Команда Mattermost: name=`test`, id=`b7u9rycm43nip86mdiuqsxdcbe`.
- Сервис `test-files` (порт 9000) — простой HTTP server для локальных вложений (`infra/test-data/`). Мини-архив Slack ссылается на `http://test-files:9000/...` для быстрой, оффлайн-доступной проверки.

Запуск:
```bash
cd infra
docker compose -f docker-compose.dev.yml up --build -d
```

Доступ:
- Backend: http://localhost:8000
- Frontend: http://localhost:5173
- Mattermost: http://localhost:8065
- Test files: http://localhost:9000
- Postgres: localhost:5432 (user/pass/db: slack-mm)

#### Frontend: immutable + ephemeral deps
- Каталог исходников фронтенда монтируется read-only (RO) внутрь контейнера (`../frontend:/app:ro`).
- При старте контейнера создаётся внутренняя рабочая директория `/workspace`, выполняется `npm ci` и запускается `vite` из неё.
- Симлинки `src` и `index.html` указывают на исходники в `/app`, что обеспечивает live reload без записи в RO слой.
- Любые временные файлы (кэш оптимизации, timestamp конфигов) живут только в `/workspace` и исчезают после остановки контейнера.
- Добавление зависимостей: редактировать `package.json` локально → `docker compose build frontend` → `docker compose up -d frontend --force-recreate`.

### Production (prod)
- Запуск: backend, frontend, persistent Postgres (volume)
- Mattermost не поднимается
- - Название команды Mattermost задаётся через переменную окружения MM_TEAM в .env (например, MM_TEAM=yourteam).
- Для запуска:
  ```bash
  cd infra
  docker-compose -f docker-compose.prod.yml up --build
  ```
- Доступ:
  - Backend: http://localhost:8000
  - Frontend: http://localhost (порт 80)
  - Postgres: localhost:5432 (user/pass/db: slack-mm)

## Ephemeral storage
- В dev-окружении все сервисы используют tmpfs (эфемерное хранилище): данные теряются при остановке контейнеров.
- В prod-окружении Postgres использует volume `db_data` для сохранения данных.

## Cursor instructions
- В директории infra всегда должен быть актуальный README.md с описанием структуры и назначения.
- При добавлении новых файлов/директорий — обновлять описание.
- Все соглашения и инструкции фиксируются в этом README.md.
- Пример: если добавлен новый манифест, скрипт или плагин, добавить его описание в раздел "Структура".

## Соглашения
- Все изменения отражаются в этом README. 

## Профилактика «залипания» dev-сервисов

- Для корректной обработки сигналов и завершения процессов добавлены параметры в `docker-compose.dev.yml`:
  - `init: true`, `stop_signal: SIGINT`, `stop_grace_period: 10s` для `backend` и `frontend`;
  - `init: true`, `stop_signal: SIGINT`, `stop_grace_period: 15s` для `mattermost`.
- Запуск фронтенда переведён на прямой `npx vite --host` (вместо `npm run dev`), чтобы PID 1 принадлежал самому `node/vite` и правильно завершался.
- Рекомендации при разработке:
  - Перед повторным запуском: `docker compose -f infra/docker-compose.dev.yml down --remove-orphans`.
  - При проблемах после сна/перезапуска Docker/WSL: перезапустить демон Docker или выполнить `wsl.exe --shutdown` (из Windows) для очистки namespaces/cgroups.
  - Если порт занят осиротевшим процессом: завершить процесс и повторно поднять сервисы.

## Мини-интеграционный тестовый скрипт

Скрипт `scripts/run_mini_backup_integration.sh` (из корня репозитория) автоматизирует проверку:

1. Полный поднятие стека (dev compose).
2. Авто ensure плагина (deploy/enable при необходимости).
3. Загрузка мини-архива и ожидание завершения джоба.
4. Проверка финальных чисел (users/channels/messages/attachments/reactions) и отсутствие ошибок в логах.
5. Валидация корректного сопоставления admin пользователя.

Запуск:
```bash
./scripts/run_mini_backup_integration.sh
```

Используйте перед коммитом, меняющим пайплайн импорта/экспорта.