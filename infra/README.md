# Инфраструктура

Каталог содержит файлы для локальной инфраструктуры проекта.

## Docker Compose

1. Подготовьте переменные окружения:
   ```bash
   cp infra/.env.example infra/.env
   ```
   Файл читается как локальным Django (через `infra/.env`), так и Docker Compose.

2. Запуск отдельных сервисов:
   ```bash
   cd infra
   docker compose up postgres         # только СУБД
   docker compose up web              # Django + runserver (порт 8000)
   docker compose up collectors       # воркер очереди collector
   docker compose up collectors_web   # воркер очереди collector_web
   ```
   Контейнеры используют общий образ из `infra/Dockerfile`, автоматически подгружают код через volume `../:/app` и читают переменные из `infra/.env`. Переменная `POSTGRES_HOST` внутри контейнеров переопределяется на `postgres`, поэтому локальные запуски по-прежнему могут оставлять `127.0.0.1`.

3. Параметры можно менять через `.env`. Например, чтобы замедлить телеграм-сборщик, добавьте `COLLECTOR_SLEEP=15` и перезапустите сервис.

4. Полное выключение стеков:
   ```bash
   cd infra
   docker compose down
   ```

> Данные PostgreSQL сохраняются в именованном Docker-томе `paperbird-postgres-data`.
