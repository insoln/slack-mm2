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

### Dev окружение (Docker)
Фронтенд запускается через Docker Compose (см. `infra/docker-compose.dev.yml`). При старте выполняется `npm ci`, затем запускается Vite dev server на порту 5173 (hot reload).

Запуск (совместно с backend):
```bash
docker compose -f infra/docker-compose.dev.yml up --build frontend backend
```

Обновление зависимостей после изменения package.json:
```bash
docker compose -f infra/docker-compose.dev.yml build frontend
docker compose -f infra/docker-compose.dev.yml up -d frontend --force-recreate
```

Production build (опционально):
```bash
docker build -t slack-mm2-frontend:prod -f frontend/Dockerfile .
```
Дальше можно раздавать статический build через любой HTTP сервер.

### Планируемые улучшения
* Кеш npm (`/root/.npm`) через buildkit cache mount
* E2E smoke тест (Playwright)
* Вынос общих ENV (API base URL) в `.env`
* Возможный многостадийный Dockerfile с кешем зависимостей
