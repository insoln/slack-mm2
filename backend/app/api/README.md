# api/

Роуты FastAPI для Slack-MM2 Sync backend.

- upload.py — эндпоинты для загрузки и обработки файлов бэкапа
- export.py — эндпоинты для запуска экспорта данных в Mattermost
- webhook.py — эндпоинты для приёма и обработки вебхуков

В этом модуле не должно быть бизнес-логики — только валидация входных данных и вызовы сервисов.

## Пример подключения роутера

```python
from app.api.upload import router as upload_router
from app.api.export import router as export_router
app.include_router(upload_router)
app.include_router(export_router)
``` 