# Frontend (React)

Корпоративная панель для загрузки бэкапа Slack, управления плагином и запуска экспорта.

## Структура
- `src/` — исходный код React приложения
  - `components/UI.jsx`, `components/ui.css` — переиспользуемые компоненты (Header, Sidebar, Card, Button, StatusBadge) и тема
  - `App.jsx` — разметка корпоративной панели (шапка, сайдбар, карточки)
- `public/` — статические файлы
- `Dockerfile` — контейнеризация
- `package.json` — зависимости

## Возможности UI
- Загрузка архивов Slack (progress bar)
- Управление плагином Mattermost Importer: статус, Deploy, Enable, Ensure
- Запуск экспорта данных

## Темизация
- Базовая тёмная тема с CSS-переменными (см. `components/ui.css`). Можно расширить под бренд.

## Разработка
- Приложение на Vite + React. Компоненты без сторонних библиотек, легко стилизуются.

## Политика сборки (ВАЖНО)
Локальная сборка через `npm run build` вне Docker НЕ поддерживается. Используем только `docker compose`

## Эфемерная dev-среда (коротко)
Dev контейнер каждый старт выполняет `npm ci` в рабочей директории (`/workspace`), исходники монтируются read‑only. Это гарантирует чистые зависимоcти и отсутствие «залипших» node_modules. Для добавления пакета: обновите `package.json` локально и пересоберите образ `frontend` через compose build.

## TODO (возможные улучшения)
- Добавить отдельный production compose профиль.
- Вынести общие ENV (API base URL) в `.env` с примером.
- Добавить e2e smoke тест (Playwright) в CI.
- Кеш npm через buildkit cache mount.

#### Обновление зависимостей
```bash
npm update                # локально, затем rebuild
docker compose -f infra/docker-compose.dev.yml build frontend
docker compose -f infra/docker-compose.dev.yml up -d frontend --force-recreate
```

#### Ограничения
- Старт медленнее (полный `npm ci` ~7–9s) — плата за чистоту и отсутствие кэша.
- Нельзя писать в `/app` внутри контейнера (это ожидаемо). Любые ошибки `EROFS` означают, что что-то ещё пытается кэшироваться вне `/workspace`.

#### Как понять что всё работает
В логах контейнера `frontend` после старта:
```
[entrypoint] Installing dependencies (ephemeral)
...
VITE vX.Y.Z  ready in N ms
```
Если видите повторяющийся цикл install → crash — проверьте, не добавлен ли флаг, не поддерживаемый текущей версией Vite.

#### Добавление dev-only инструментов
Просто добавьте их в `devDependencies`, пересоберите образ. Они попадут в слой, но устанавливаться всё равно будут заново при старте — если это критично по времени, можно рассмотреть многостадийный Dockerfile с кешированием (`--mount=type=cache,target=/root/.npm`). Пока сознательно упрощено.

#### Возможные улучшения (отложено)
- Кеш npm (`/root/.npm`) через buildkit cache mount.
- Предварительная оптимизация Vite deps в образе (можно через `vite build --ssr config-only`).
- Watch через polling отключить, если FS события надёжны у вашей платформы.
>>>>>>> 1c59c28 (frontend: ephemeral workspace dev env (RO bind + /workspace deps) + docs; add entrypoint refactor docs references)
