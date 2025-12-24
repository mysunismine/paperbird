# Public Pages for Published Stories

## 1. Цели
- Публичная витрина опубликованных материалов каждого проекта в формате «мини‑блога».
- Единые канонические ссылки на публикации (для внешних читателей и SEO).
- Прозрачная привязка к статусам публикаций и медиа, без доступа к редакторским данным.

## 2. URL‑схема
- Каталог проектов: `/public/`  
- Лента проекта: `/public/<project_id>/`  
- Материал: `/public/<project_id>/p/<publication_id>/`

Почему без slug:
- Проще в реализации и гарантирует уникальность.
- При необходимости можно добавить slug позже (`/public/<project_id>/<slug>/`), не ломая существующие ссылки.

## 3. Источник данных
- **Publication**: только `status=published`, `published_at`, `result_text`, `message_ids`, `media_order`.
- **Story**: `title`, `summary`, `body`, `images`.
- **StoryImage**: `image_file`, `source_kind=generated|upload|source`.

Правило отбора:
- В ленту попадают только публикации со статусом `published`.
- Для страницы публикации используется `Publication.result_text` (как фактически опубликованный текст).
- Если `result_text` пуст — fallback на `Story.body`.

## 4. Визуальная структура
Лента:
- Заголовок проекта.
- Список карточек: заголовок, дата публикации, короткий лид (summary или первые N символов текста), превью изображения (первое доступное).
- Пагинация.

Материал:
- Заголовок, дата, текст публикации.
- Блок изображений (из `StoryImage`) с fallback на изображения из постов.
- Кнопка «Открыть в Telegram» (если `Publication.message_url()` доступен).

## 5. Доступ и безопасность
- Публичная страница не показывает редакторские поля (`editor_comment`, промпты и т.д.).
- Возможность отключить публичные страницы на уровне проекта (флаг `public_enabled`).
- Ограничение индексации (настройка `public_noindex` для приватных проектов).

## 6. Модельные изменения (если потребуется)
- `Project.public_enabled` (bool, default False)
- `Project.public_noindex` (bool, default True)
- `Project.public_title` (опционально)

## 7. Техническая реализация (MVP)
1) Создать публичные Django‑views:
   - `PublicProjectView` (лента)
   - `PublicPublicationView` (детальная)
2) Шаблоны:
   - `templates/public/project.html`
   - `templates/public/publication.html`
3) Контекст:
   - Только `Publication` + `Story` + `StoryImage`.
4) Пагинация 10–20 элементов.
5) Минимальные стили (легкий, «читабельный» режим).

## 8. SEO и каноничность
- `<title>` = заголовок публикации или проекта.
- `og:title`, `og:description`, `og:image`.
- `canonical` на страницу публикации.
- `noindex` при `public_noindex=True`.

## 9. Открытые вопросы
- Нужен ли домен/поддомен для публичных страниц.
- Как формировать превью для Telegram‑публикаций без `result_text`.
- Нужна ли карта сайта.
