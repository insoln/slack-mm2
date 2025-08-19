# api/

Роуты FastAPI для Slack-MM2 Sync backend.

- upload.py — эндпоинты для загрузки и обработки файлов бэкапа
- export.py — эндпоинты для запуска экспорта данных в Mattermost
- plugin.py — эндпоинты управления плагином Mattermost (status/deploy/enable/ensure)

В этом модуле не должно быть бизнес-логики — только валидация входных данных и вызовы сервисов.

## Эндпоинты

- POST /export — запуск фонового экспорта
- GET  /plugin/status — состояние плагина (установлен/включен/версия/наличие бандла)
- POST /plugin/deploy — загрузить локальный бандл плагина в Mattermost (с попыткой сборки при отсутствии)
- POST /plugin/enable — включить плагин
- POST /plugin/ensure — обеспечить: установлен актуальный бандл и включен

## Пример подключения роутера

```python
from app.api.upload import router as upload_router
from app.api.export import router as export_router
from app.api.plugin import router as plugin_router
app.include_router(upload_router)
app.include_router(export_router)
app.include_router(plugin_router)
```