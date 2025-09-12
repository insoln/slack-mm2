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

## Как запустить (dev)
```bash
docker compose -f infra/docker-compose.dev.yml up --build frontend backend
```
Особенности dev:
* Vite dev server внутри контейнера (hot reload).
* Код примонтирован как volume — редактируете локально, браузер обновляется.
* Node_modules кэшируются в named volume (см. compose), чтобы ускорить повторные сборки.

## Production build
Продакшн-слой также собираем только через Docker. Пример (если понадобится отдельный прод-слой для фронта):
```bash
docker build -t slack-mm2-frontend:prod -f frontend/Dockerfile .
```
А затем можно запустить статический сервер (если Dockerfile настроен на `npm run build` + serve, либо через nginx). На текущий момент достаточно dev-компоновки, т.к. интерфейс внутренний.

## Частые задачи
| Задача | Команда |
|--------|---------|
| Обновить контейнеры после изменения package.json | `docker compose -f infra/docker-compose.dev.yml build frontend` |
| Принудительно пересобрать без кеша | `docker compose -f infra/docker-compose.dev.yml build --no-cache frontend` |
| Перезапустить только фронт | `docker compose -f infra/docker-compose.dev.yml up -d frontend` |

## Тестирование изменений фронта
1. Внести правку в `src/`.
2. Убедиться что авто‑пересборка прошла (логи контейнера `frontend`).
3. Проверить UI в браузере.
4. При изменении зависимостей: пересобрать образ фронта (см. таблицу выше).

## TODO (возможные улучшения)
- Добавить отдельный production compose профиль.
- Вынести общие ENV (API base URL) в `.env` с примером.
- Добавить e2e smoke тест (Playwright) в CI.
