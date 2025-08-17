# Infra

Инфраструктурные файлы для деплоя приложения в Kubernetes, настройки БД, Mattermost и CI/CD.

## Структура
- `k8s/` — манифесты Kubernetes
- `db/` — миграции и инициализация Postgres
- `plugin/` — Mattermost plugin для импорта сообщений и метаданных из внешних систем (см. подробности в plugin/README.md)
- `mattermost/` — конфиг Mattermost (`mm_config.json`)
- `docker-compose.dev.yml` — dev-окружение: backend, frontend, ephemeral Postgres, ephemeral Mattermost
- `docker-compose.prod.yml` — prod-окружение: backend, frontend, persistent Postgres
- `docker-compose.yml` — (по умолчанию, можно использовать как prod)

## Окружения

### Development (dev)
- Запуск: backend, frontend, Mattermost, ephemeral Postgres (tmpfs, все данные теряются при остановке)
- Mattermost использует конфиг из `mattermost/mm_config.json`
- - Backend в dev-окружении подключается к Mattermost по адресу `http://mattermost:8065` (см. переменную окружения `MM_URL` в docker-compose.dev.yml)
- - Для работы backend требуется токен пользователя Mattermost (`MM_TOKEN`). В dev-окружении токен всегда одинаковый и предзадан в init-mattermost.sql:
  - MM_TOKEN=5x7rr788c7gwdnkdr9imb49ffo
- - Название команды Mattermost в dev-окружении всегда 'Test' (name: test, id: b7u9rycm43nip86mdiuqsxdcbe) — см. init-mattermost.sql.
- Для запуска:
  ```bash
  cd infra
  docker-compose -f docker-compose.dev.yml up --build
  ```
- Доступ:
  - Backend: http://localhost:8000
  - Frontend: http://localhost:5173
  - Mattermost: http://localhost:8065
  - Postgres: localhost:5432 (user/pass/db: slack-mm)

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