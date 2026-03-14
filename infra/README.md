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
   docker compose up postgres              # только СУБД
   docker compose up watercrawl            # только Watercrawl API
   docker compose up paperbird             # Django + runserver (порт 8000)
   docker compose --profile workers up collectors collectors_web
   ```
   Контейнеры используют общий образ из `infra/Dockerfile`, автоматически подгружают код через volume `../:/app` и читают переменные из `infra/.env`. Сервисы `paperbird` и `watercrawl` работают во внутренней сети `paperbird-internal`: Django обращается к Watercrawl по адресу `http://watercrawl:8080/...`.

3. Параметры можно менять через `.env`. Например, чтобы замедлить телеграм-сборщик, добавьте `COLLECTOR_SLEEP=15` и перезапустите сервис.

4. Полное выключение стеков:
   ```bash
   cd infra
   docker compose down
   ```

> Данные PostgreSQL сохраняются в именованном Docker-томе `paperbird-postgres-data`.
