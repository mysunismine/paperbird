# Deploy & Run Guide

## 1. Требования к окружению

- **Python:** версия 3.8 и выше  
- **Зависимости:** указаны в `requirements.txt` или `Pipfile`  
- **База данных:** PostgreSQL 12+ (рекомендуется) или другая совместимая СУБД  
- **Сторонние сервисы:**  
  - Redis (для очередей задач и кэширования)  
  - SMTP-сервер (для отправки почты, если используется)  

## 2. Подготовка окружения

1. Клонируйте репозиторий проекта:  
   ```bash
   git clone <репозиторий>
   cd <папка_проекта>
   ```

2. Создайте виртуальное окружение и активируйте его:  
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # Linux/MacOS
   venv\Scripts\activate     # Windows
   ```

3. Установите зависимости:  
   ```bash
   pip install -r requirements.txt
   ```

4. Создайте файл `.env` в корне проекта (см. раздел 3).

## 3. Конфигурация `.env`

Пример обязательных переменных окружения:

```
DJANGO_SECRET_KEY=<секретный_ключ_приложения>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,yourdomain.com

DATABASE_URL=postgres://user:password@localhost:5432/dbname

REDIS_URL=redis://localhost:6379/0

EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=your_email@example.com
EMAIL_HOST_PASSWORD=your_email_password
EMAIL_USE_TLS=True
```

- `DJANGO_SECRET_KEY` — секретный ключ Django  
- `DJANGO_DEBUG` — режим отладки (True/False)  
- `DJANGO_ALLOWED_HOSTS` — список разрешённых хостов через запятую  
- `DATABASE_URL` — строка подключения к базе данных  
- `REDIS_URL` — адрес Redis-сервера  
- Переменные для почты — если отправка почты настроена  

## 4. Миграции и база данных

1. Инициализируйте базу данных (если она ещё не создана).  
2. Примените миграции:  
   ```bash
   python manage.py migrate
   ```

3. (Опционально) Создайте суперпользователя:  
   ```bash
   python manage.py createsuperuser
   ```

## 5. Запуск приложения локально

- Запуск Django-сервера:  
  ```bash
  python manage.py runserver
  ```

- Поднятие локальной базы данных через Docker Compose:  
  ```bash
  cd infra
  docker compose up -d
  ```
  Используются переменные окружения `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (по умолчанию `paperbird`). Данные хранятся в Docker-томе `paperbird-postgres-data`.

- Сбор постов из Telegram (локально):  
  ```bash
  python manage.py collect_posts <username> --limit 50
  ```
  Убедитесь, что в профиле пользователя заполнены ключи Telethon и запущена база данных.

- Запуск Celery (если используется):  
  ```bash
  celery -A <проект> worker -l info
  ```

- Запуск планировщика задач (beat):  
  ```bash
  celery -A <проект> beat -l info
  ```

- Запуск RQ worker (если используется):  
  ```bash
  rq worker
  ```

## 6. Запуск фоновых задач

Для запуска фоновых задач (сборщик постов, рерайтер, публикатор, генератор изображений) используйте соответствующие команды или скрипты, например:

```bash
python manage.py run_post_collector
python manage.py run_rewriter
python manage.py run_publisher
python manage.py run_image_generator
```

Если задачи реализованы через Celery или RQ, убедитесь, что воркеры запущены (см. раздел 5).

## 7. Запуск через Docker

Пример `docker-compose.yml`:

```yaml
version: '3'

services:
  web:
    build: .
    command: python manage.py runserver 0.0.0.0:8000
    volumes:
      - .:/app
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      - db
      - redis

  db:
    image: postgres:12
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: password
      POSTGRES_DB: dbname
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:6

volumes:
  pgdata:
```

Команды для запуска:  
```bash
docker-compose build
docker-compose up
```

## 8. CI/CD и развёртывание в продакшен

Основные шаги при деплое:

1. **Build:** собрать образ или установить зависимости  
2. **Migrate:** применить миграции  
   ```bash
   python manage.py migrate --noinput
   ```
3. **Collectstatic:** собрать статические файлы  
   ```bash
   python manage.py collectstatic --noinput
   ```
4. **Restart:** перезапустить сервисы (Gunicorn, Celery, планировщик и т.д.)  
5. **Проверить логи** и состояние сервисов

## 9. Мониторинг и логи

- Логи Django по умолчанию выводятся в консоль или в файлы, если настроено.  
- Логи Celery и RQ воркеров также выводятся в консоль или файл.  
- Для мониторинга фоновых задач используйте:  
  - [Flower](https://flower.readthedocs.io/en/latest/) для Celery  
  - [RQ Dashboard](https://github.com/rq/django-rq#rq-dashboard) для RQ  
- Ошибки и исключения можно отслеживать через Sentry или аналогичные сервисы (если настроены).

## 10. Частые проблемы и их решения

- **Ошибка подключения к базе данных:** проверьте правильность `DATABASE_URL` и доступность сервера БД.  
- **Миграции не применяются:** убедитесь, что виртуальное окружение активировано и зависимости установлены.  
- **Celery воркер не запускается:** проверьте правильность настройки `REDIS_URL` и доступность Redis.  
- **Статические файлы не отображаются:** выполните `collectstatic` и настройте сервер для их отдачи.  
- **Переменные окружения не подхватываются:** убедитесь, что `.env` файл находится в корне и загружается (например, с помощью `django-environ`).  
- **Проблемы с правами доступа:** проверьте права на файлы и папки проекта.

Если проблема не решается, проверьте логи и обратитесь к документации используемых инструментов.
