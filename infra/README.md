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
   docker compose up -d postgres
   docker compose up -d watercrawl-postgres watercrawl-redis watercrawl watercrawl-worker
   docker compose up -d paperbird          # Django + runserver (порт 8000)
   docker compose --profile workers up collectors collectors_web
   ```
   Контейнеры используют общий образ из `infra/Dockerfile`, автоматически подгружают код через volume `../:/app` и читают переменные из `infra/.env`. Сервисы `paperbird` и `watercrawl` работают во внутренней сети `paperbird-internal`: Django обращается к Watercrawl по адресу `http://watercrawl:8080/...`.
   При старте `watercrawl` автоматически:
   - применяет миграции и собирает статику;
   - создаёт пользователя (если его нет);
   - создаёт API-ключ команды и сохраняет его в общий том `watercrawl-shared`;
   - `paperbird` читает ключ из `WATERCRAWL_API_KEY_FILE` и может работать без ручной настройки ключа.

3. Параметры можно менять через `.env`. Например, чтобы замедлить телеграм-сборщик, добавьте `COLLECTOR_SLEEP=15` и перезапустите сервис.

4. Полное выключение стеков:
   ```bash
   cd infra
   docker compose down
   ```

> Данные PostgreSQL сохраняются в именованном Docker-томе `paperbird-postgres-data`.
> Данные Watercrawl (PostgreSQL, media, static) сохраняются в `paperbird-watercrawl-postgres-data` и `paperbird-watercrawl-data`.
