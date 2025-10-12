# api/

Роуты FastAPI для Slack-MM2 Sync backend.

- upload.py — эндпоинты для загрузки и обработки файлов бэкапа
- export.py — эндпоинты для запуска экспорта данных в Mattermost
- plugin.py — эндпоинты управления плагином Mattermost (status/deploy/enable/ensure)

Стадии импорта/экспорта однопроходные (extracting → users → channels → messages → exporting → done). Отдельных стадий reactions/attachments/emojis больше нет — всё в messages.

В этом модуле не должно быть бизнес-логики — только валидация входных данных и вызовы сервисов.

## Эндпоинты

- POST /export — запуск фонового экспорта
- GET  /plugin/status — состояние плагина: установлен/включен/версии/наличие бандла + метаданные (`bundle_sha256`, `bundle_mtime`, `bundle_size`)
- GET  /plugin/bundle/info — только метаданные бандла (404 если отсутствует)
- POST /plugin/deploy — загрузить уже собранный локальный бандл (не строит)
- POST /plugin/enable — включить плагин
- POST /plugin/ensure — обеспечить: при наличии бандла установлен актуальный и включен (без сборки)
- GET  /jobs — (агрегирующий статус импортов) возвращает детерминированные *_processed счётчики без отката назад и стадию текущего/последнего job.

### Ответ /plugin/status

```jsonc
{
	"plugin_id": "mm-importer",
	"expected_version": "0.1.0",
	"installed": true,
	"enabled": true,
	"installed_version": "0.1.0",
	"needs_update": false,
	"bundle_exists": true,
	"bundle_path": "/app/infra/plugin/dist/mm-importer-0.1.0.tar.gz",
	"bundle_sha256": "1f8c0d...",
	"bundle_mtime": 1726123456,
	"bundle_size": 48321,
	"bundle_hash_computed_at": 1726123460
}
```

`bundle_hash_computed_at` — epoch (секунды) когда вычислен hash (кэш обновляется при смене mtime/size).

## Пример подключения роутера

```python
from app.api.upload import router as upload_router
from app.api.export import router as export_router
from app.api.plugin import router as plugin_router
app.include_router(upload_router)
app.include_router(export_router)
app.include_router(plugin_router)
```