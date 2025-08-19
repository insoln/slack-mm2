# app/

Исходный код FastAPI-приложения.

- `main.py` — основной файл приложения, реализует healthcheck и регистрацию роутов. 
- На старте выполняются миграции Alembic и выполняется best-effort auto-ensure плагина импорта для Mattermost (см. `app/api/plugin.py`).

## Переменные окружения
- `DATABASE_URL` — строка подключения к Postgres (asyncpg)
- `MM_URL` — базовый URL Mattermost
- `MM_TOKEN` — токен администратора Mattermost
- `MM_TEAM` — имя команды (для резолва team_id)
- `MM_TEAM_ID` — явный ID команды (опционально)
- `EXPORT_WORKERS` — число параллельных воркеров экспорта

## Проверки
- Healthcheck: `GET /healthcheck`
- Плагин:
  - `GET /plugin/status` — состояние/версия/бандл
  - `POST /plugin/ensure` — установить/обновить и включить плагин

**Все новые обработчики и тяжёлые операции должны быть реализованы через async def. Upload отдаёт ответ об успехе/неуспехе сразу после завершения загрузки файла, до парсинга.**