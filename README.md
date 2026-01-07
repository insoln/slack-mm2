# Slack-MM2 Sync

Приложение для односторонней синхронизации данных из Slack в Mattermost.

## Для пользователей

Этот документ содержит инструкции по установке и эксплуатации в production-окружении.

**Для разработчиков**: См. [Developer Documentation](docs/dev.md) — полная информация по архитектуре, локальной разработке, тестированию и сборке.

## Структура проекта

- `backend/` — Python FastAPI backend, реализующий API, обработку загрузки файлов и экспорт данных
- `frontend/` — React frontend, веб-интерфейс для загрузки файлов и мониторинга
- `infra/` — инфраструктура, Docker Compose конфигурации и Kubernetes манифесты
  - `plugin/` — исходники Mattermost плагина (Go)
  - См. [`infra/README.md`](infra/README.md) для детальной информации по инфраструктуре

## Запуск в Production

### 1. Подготовка конфигурации

Создайте файл `infra/.env.prod` с реальными токенами и URL вашего Mattermost:

```bash
cat > infra/.env.prod <<'EOF'
MM_URL=https://mattermost.example.com
MM_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxx
MM_TEAM=yourteam
SLACK_VERIFICATION_TOKEN=your_slack_verification_token
SLACK_BOT_TOKEN=your_slack_bot_token
SLACK_SIGNING_SECRET=your_slack_signing_secret
EOF
```

**Важно**: Все переменные должны быть сконфигурированы с реальными значениями для вашей инсталляции.

### 2. Запуск production-стека

```bash
cd infra
docker compose -f docker-compose.prod.yml up --build -d
```

Сервисы будут доступны по следующим адресам:
- **Backend API**: http://localhost:8000
- **Frontend UI**: http://localhost (порт 80)
- **PostgreSQL**: localhost:5432 (user/pass/db: slack-mm)

### 3. Сборка и развертывание Mattermost плагина

После первого запуска необходимо собрать и задеплоить плагин:

```bash
# Соберите плагин
cd plugin
bash build-docker.sh
cd ..

# Дождитесь готовности backend
curl http://localhost:8000/healthcheck

# Задеплойте и включите плагин
curl -X POST http://localhost:8000/api/plugin/deploy
curl -X POST http://localhost:8000/api/plugin/enable

# Проверьте статус
curl http://localhost:8000/api/plugin/status
```

Подробнее см. [`infra/README.md`](infra/README.md) и [`backend/README.md`](backend/README.md).

## Обновление проекта

Процедура обновления инсталляции:

1. **Остановите стек**:
   ```bash
   cd infra
   docker compose -f docker-compose.prod.yml down
   ```

2. **Обновите код** до последней версии:
   ```bash
   git pull origin master
   ```

3. **Пересоберите плагин** (если были изменения в `infra/plugin/`):
   ```bash
   cd plugin
   bash build-docker.sh
   cd ..
   ```

4. **Пересоберите и запустите обновленный стек**:
   ```bash
   docker compose -f docker-compose.prod.yml up --build -d
   ```

5. **Задеплойте обновленный плагин** (если пересобирали на шаге 3):
   ```bash
   curl -X POST http://localhost:8000/api/plugin/deploy
   curl -X POST http://localhost:8000/api/plugin/enable
   ```

**Важно**: 
- Миграции БД применяются автоматически при старте backend через Alembic. Первый запуск после обновления может занять несколько минут в зависимости от объема данных. Backend станет доступен после успешного выполнения всех миграций.
- Плагин нужно пересобирать и деплоить только при изменениях в коде плагина. Подробнее см. раздел "Управление Mattermost Plugin" ниже.

Дополнительная информация по обновлениям и миграциям данных: [`backend/README.md`](backend/README.md).

## Управление Mattermost Plugin

### Сборка и развертывание плагина

**Важно**: В production окружении плагин **не собирается автоматически**. Вам необходимо вручную собрать и задеплоить плагин при первом запуске и при каждом обновлении проекта.

#### Процедура сборки и развертывания:

1. **Соберите плагин** (создаст `infra/plugin/dist/mm-importer-<version>.tar.gz`):
   ```bash
   cd infra/plugin
   bash build-docker.sh
   ```
   Сборка занимает ~60-90 секунд при первом запуске.

2. **Убедитесь, что backend запущен**:
   ```bash
   curl http://localhost:8000/healthcheck
   ```

3. **Задеплойте плагин через API** (загрузит bundle в Mattermost):
   ```bash
   curl -X POST http://localhost:8000/api/plugin/deploy
   ```

4. **Включите плагин**:
   ```bash
   curl -X POST http://localhost:8000/api/plugin/enable
   ```

5. **Проверьте статус**:
   ```bash
   curl http://localhost:8000/api/plugin/status
   ```

**При обновлении проекта** повторите шаги 1-4, чтобы пересобрать и задеплоить новую версию плагина.

### HTTP API для управления плагином

Backend предоставляет следующие эндпоинты:

| Эндпоинт | Метод | Назначение |
|----------|-------|------------|
| `/api/plugin/status` | GET | Агрегированное состояние плагина (enabled, local/remote bundle, версия) |
| `/api/plugin/ensure` | POST | Убедиться, что bundle установлен (скачает при необходимости) |
| `/api/plugin/deploy` | POST | Загрузить новый bundle в Mattermost |
| `/api/plugin/enable` | POST | Включить установленный плагин |
| `/api/plugin/reinstall` | POST | Принудительная переустановка bundle |

**Пример использования**:
```bash
# Проверить статус
curl http://localhost:8000/api/plugin/status

# Убедиться, что плагин установлен и включен
curl -X POST http://localhost:8000/api/plugin/ensure

# Включить плагин
curl -X POST http://localhost:8000/api/plugin/enable
```

Подробнее о сборке и развертывании плагина: [`infra/plugin/README.md`](infra/plugin/README.md).

## CI/CD и контроль качества

Проект включает автоматические проверки качества кода и тесты.

### Для пользователей

В production окружении тесты выполняются автоматически через CI/CD pipeline при каждом обновлении кода.

### Для разработчиков

Полная информация по настройке pre-commit хуков, запуску тестов и линтеров: [Developer Documentation](docs/dev.md#cicd).

## Документация компонентов

Подробная документация по каждому компоненту:

- **[Developer Documentation](docs/dev.md)** — архитектура, разработка, тестирование
- **[Backend](backend/README.md)** — FastAPI приложение, API, экспорт данных
- **[Frontend](frontend/README.md)** — React интерфейс, UI компоненты
- **[Infrastructure](infra/README.md)** — Docker Compose, Kubernetes, деплой
- **[Plugin](infra/plugin/README.md)** — Mattermost плагин, сборка
- **[Database](infra/db/README.md)** — Схема БД, миграции

### Специфические функции

- [Перезапуск задач (Job Restart)](docs/job-restart-feature.md) — механизм повторного запуска задач
- [Политика документации](docs/documentation-policy.md) — правила создания документации

## Политика ветвления (Branching Policy)

Все изменения вносятся ТОЛЬКО через отдельные ветки. Запрещено коммитить напрямую в `master`.

**Типовой поток**:
1. Обновить master: `git fetch origin && git checkout master && git pull --rebase`
2. Создать ветку: `git checkout -b feature/<кратко>` (или `fix/`, `chore/`, `docs/`)
3. Вносить изменения, запускать тесты перед пушем
4. Актуализировать ветку: `git fetch origin && git rebase origin/master`
5. Пуш: `git push -u origin feature/<кратко>`
6. Открыть Pull Request → CI → ревью → merge
7. Удалить ветку

**Жёсткие правила**:
- Никаких force push в `master`
- `master` всегда в рабочем состоянии (тесты зелёные, миграции валидны)
- Хотфикс: `hotfix/<issue>` + PR, даже при срочности

## Лицензия и контакты

Проект находится в активной разработке. За вопросами обращайтесь через GitHub Issues.

