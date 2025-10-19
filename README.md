# Paperbird

Базовый каркас локального сервиса на Django 5.1 для работы с данными из Telegram-каналов.

## Стек
- Python 3.13
- Django 5.1
- PostgreSQL
- Telethon — клиент Telegram API
- Ruff — статический анализ и автоформатирование
- Bootstrap 5 — быстрый UI в шаблонах Django

## Подготовка окружения
1. Установите Python 3.13 и PostgreSQL (локально или через Docker).
2. Создайте виртуальное окружение:
   ```bash
   python3.13 -m venv .venv
   source .venv/bin/activate
   ```
3. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
4. Создайте базу данных и пользователя в PostgreSQL:
   ```sql
   CREATE DATABASE paperbird;
   CREATE USER paperbird WITH PASSWORD 'paperbird';
   GRANT ALL PRIVILEGES ON DATABASE paperbird TO paperbird;
   ```
5. Скопируйте `.env.example` в `.env` и заполните значения:
   ```bash
   cp .env.example .env
   ```
   Обратите внимание на `DJANGO_SECRET_KEY`, `POSTGRES_*`, а также `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` и `OPENAI_API_KEY`. Telethon ключи и строковую сессию можно получить в [кабинете разработчика Telegram](https://my.telegram.org/), а ключ OpenAI — в [личном кабинете OpenAI](https://platform.openai.com/).

## Запуск проекта
```bash
source .venv/bin/activate
python manage.py migrate
python manage.py runserver
```
После запуска приложение будет доступно на http://127.0.0.1:8000/.

### Локальная БД через Docker
```bash
cd infra
docker compose up -d
```
Контейнер поднимет PostgreSQL 16 с данными в томе `paperbird-postgres-data`. Переменные `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` берутся из окружения (по умолчанию — `paperbird`).

### Сбор постов из Telegram
1. Убедитесь, что в профиле пользователя заполнены поля Telethon (API ID, API hash, строковая сессия).
2. Запустите команду:
   ```bash
   python manage.py collect_posts <username> --limit 50
   ```
   Параметр `--project <id>` ограничит сбор одним проектом.

### Рерайт сюжетов и публикация
- Создайте сюжет на основе выбранных постов через интерфейс или API, затем запустите рерайт (используется OpenAI Chat Completions).
- После успешного рерайта сюжет получает статус `ready`, и его можно опубликовать в Telegram. Для публикации используйте API `POST /stories/{id}/publish` или административный интерфейс.
- Публикация выполняется от имени владельца проекта через Telethon; убедитесь, что у пользователя заполнены Telethon credentials. Результаты и идентификаторы сообщений сохраняются в разделе «Публикации».

### Аутентификация
- Создайте учётную запись администратора для первого входа:
  ```bash
  python manage.py createsuperuser
  ```
- Форма входа доступна по адресу http://127.0.0.1:8000/accounts/login/
- После входа используйте раздел «Профиль» для добавления Telethon API ID, hash и строковой сессии.

## Проверка кода
```bash
source .venv/bin/activate
ruff check .
```
Для автоисправления доступных нарушений:
```bash
ruff check . --fix
```

## Структура директории
- `src/` — исходники приложения
  - `src/paperbird/` — настройки проекта Django
  - `src/accounts/` — аутентификация и управление профилем пользователя
  - `src/projects/` — модели проектов, источников, постов и сборщик Telethon
  - `src/core/` — базовое приложение с главной страницей
- `src/templates/` — общие шаблоны, включая Bootstrap
- `src/static/` — пользовательские статические файлы (CSS/JS/изображения)
- `src/stories/` — домен сюжетов, рерайта и публикаций, включая интеграции с OpenAI и Telegram
- `infra/` — инфраструктура и Docker Compose для локальных сервисов
- `.env.example` — пример конфигурации окружения
- `requirements.txt` — список зависимостей
- `pyproject.toml` — конфигурация Ruff
- инфраструктурные файлы (Docker, CI, конфиги) располагаются рядом с `src/`

## Дальнейшие шаги
После получения ТЗ можно приступить к реализации интеграции с Telegram через Telethon, разработке моделей и бизнес-логики, а также настройке фоновых задач для планового обновления данных.
