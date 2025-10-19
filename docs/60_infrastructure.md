# Инфраструктура проекта

## Каталог `infra/`
- `docker-compose.yml` — запуск локальной базы данных PostgreSQL 16.
- `README.md` — инструкции по работе с инфраструктурой.

## Быстрый старт
```bash
cd infra
POSTGRES_PASSWORD=paperbird docker compose up -d
```

По умолчанию используются переменные окружения из корня проекта. После запуска можно выполнять миграции (`python manage.py migrate`) и работать с приложением.
