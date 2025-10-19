# Инфраструктура

Каталог содержит файлы для локальной инфраструктуры проекта.

## Docker Compose

Запуск локальной базы данных PostgreSQL:

```bash
cd infra
POSTGRES_PASSWORD=paperbird docker compose up -d
```

По умолчанию используются значения из `.env` проекта. Если переменные `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` не заданы, будут применены значения `paperbird`/`paperbird`.

После запуска БД можно выполнить миграции из корня проекта:

```bash
python manage.py migrate
```

Остановка и удаление контейнера:

```bash
cd infra
docker compose down
```

> **Примечание:** данные сохраняются в именованном Docker-томе `paperbird-postgres-data`.
