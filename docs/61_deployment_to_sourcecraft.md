# Deployment to SourceCraft

This document provides instructions for deploying the Paperbird Django application to SourceCraft.

## Overview

Paperbird is a Django 5.1 application that integrates with Telegram channels to collect, process, and republish content. The application uses PostgreSQL for data storage and has several background worker processes for handling different tasks.

## Application Architecture

### Core Components

1. **Web Interface**: Django application serving the main UI
2. **Background Workers**: Several worker types for different tasks:
   - `collector`: Collects posts from Telegram sources
   - `collector_web`: Collects content from web sources
   - `rewrite`: Rewrites content using AI models
   - `publish`: Publishes content to Telegram
   - `image`: Generates images for posts
   - `maintenance`: Performs cleanup tasks
   - `source`: Updates source metadata

3. **Database**: PostgreSQL for storing all application data
4. **Static Files**: CSS, JavaScript, and image assets
5. **Media Files**: User-uploaded content

### Dependencies

- Python 3.13
- Django 5.1
- PostgreSQL 13+
- Telethon for Telegram integration
- OpenAI API for content rewriting
- Yandex API for alternative AI models

## Deployment Requirements

### Environment Variables

The application requires several environment variables to be configured:

```
DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com

POSTGRES_DB=paperbird
POSTGRES_USER=paperbird
POSTGRES_PASSWORD=your-db-password
POSTGRES_HOST=your-db-host
POSTGRES_PORT=5432

TELEGRAM_API_ID=your-telegram-api-id
TELEGRAM_API_HASH=your-telegram-api-hash
TELEGRAM_SESSION=your-telegram-session-string

OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT=30
OPENAI_IMAGE_TIMEOUT=60

YANDEX_API_KEY=your-yandex-api-key
YANDEX_FOLDER_ID=your-yandex-folder-id
YANDEX_TIMEOUT=60
YANDEX_IMAGE_TIMEOUT=90
```

### Database Setup

The application requires a PostgreSQL database with the following configuration:
- Database name: `paperbird`
- User: `paperbird`
- Appropriate permissions for the user

### Background Workers

The application requires several background worker processes to be running:
1. `collector` worker: `python manage.py run_worker collector`
2. `collector_web` worker: `python manage.py run_worker collector_web`
3. `rewrite` worker: `python manage.py run_worker rewrite`
4. `publish` worker: `python manage.py run_worker publish`
5. `image` worker: `python manage.py run_worker image`
6. `maintenance` worker: `python manage.py run_worker maintenance`
7. `source` worker: `python manage.py run_worker source`

## Deployment Steps

### 1. Repository Setup

1. Create a new repository on SourceCraft
2. Push your application code to the repository
3. Ensure all necessary files are included:
   - Source code (`src/`)
   - Requirements (`requirements.txt`)
   - Configuration files (`infra/.env.example`)
   - Documentation (`docs/`)

### 2. Environment Configuration

1. Create environment variables in SourceCraft:
   - Set `DJANGO_DEBUG` to `False`
   - Configure `DJANGO_ALLOWED_HOSTS` with your domain
   - Set database connection parameters
   - Add API keys for Telegram, OpenAI, and Yandex

### 3. Database Migration

1. Run initial database migrations:
   ```bash
   python manage.py migrate
   ```

2. Create a superuser account:
   ```bash
   python manage.py createsuperuser
   ```

### 4. Static Files

1. Collect static files:
   ```bash
   python manage.py collectstatic --noinput
   ```

### 5. Worker Processes

Ensure all required worker processes are configured to run:
- Collector worker
- Web collector worker
- Rewrite worker
- Publish worker
- Image worker
- Maintenance worker
- Source worker

### 6. Web Server

Configure the web server to serve the Django application using WSGI.

## Maintenance

### Regular Tasks

1. Monitor worker processes to ensure they're running
2. Check logs for errors
3. Perform database backups
4. Update dependencies as needed

### Scaling Considerations

For high-traffic deployments:
1. Consider running multiple instances of each worker type
2. Use a load balancer for the web interface
3. Consider using a managed PostgreSQL service
4. Implement monitoring and alerting

## Troubleshooting

### Common Issues

1. **Database Connection Errors**: Verify database credentials and network connectivity
2. **Worker Process Failures**: Check logs for specific error messages
3. **Static File Issues**: Ensure `collectstatic` has been run
4. **API Key Problems**: Verify all API keys are correctly configured

### Logs

Check application logs for:
- Error messages
- Warning conditions
- Performance issues
- Worker process status