

# API Routes Specification

## 1. Общие принципы

- **Базовый URL:** `/api/v1/`
- **Версия API:** v1
- **Аутентификация:** JWT-токен в заголовке `Authorization: Bearer <token>`
- **Формат запросов и ответов:** JSON (`Content-Type: application/json`)
- **Ответы:** Все ответы возвращаются в формате JSON. В случае ошибки возвращается объект с описанием ошибки.

---

## 2. Аутентификация и пользователь

### `POST /auth/login`
- **Назначение:** Вход пользователя по логину и паролю.
- **Тело запроса:**
  ```json
  {
    "username": "user@example.com",
    "password": "password123"
  }
  ```
- **Пример успешного ответа:**
  ```json
  {
    "access_token": "jwt-token",
    "token_type": "bearer"
  }
  ```
- **Ошибки:** `401 Unauthorized` (неверные данные)

---

### `POST /auth/logout`
- **Назначение:** Выход пользователя (аннулирование токена).
- **Тело запроса:** Нет.
- **Пример ответа:**
  ```json
  { "detail": "Logged out" }
  ```
- **Ошибки:** `401 Unauthorized`

---

### `GET /user/me`
- **Назначение:** Получение профиля текущего пользователя.
- **Пример ответа:**
  ```json
  {
    "id": 1,
    "username": "user@example.com",
    "name": "Ivan",
    "email": "user@example.com",
    "settings": { ... }
  }
  ```
- **Ошибки:** `401 Unauthorized`

---

### `PATCH /user/settings`
- **Назначение:** Обновление настроек пользователя.
- **Тело запроса:**
  ```json
  {
    "name": "Ivan",
    "email": "user@example.com"
  }
  ```
- **Пример ответа:**
  ```json
  {
    "id": 1,
    "username": "user@example.com",
    "name": "Ivan",
    "email": "user@example.com",
    "settings": { ... }
  }
  ```
- **Ошибки:** `400 Bad Request`, `401 Unauthorized`

---

## 3. Проекты

### `GET /projects`
- **Назначение:** Получение списка проектов пользователя.
- **Пример ответа:**
  ```json
  [
    { "id": 1, "name": "Project 1", "created_at": "2024-01-01T00:00:00Z" },
    { "id": 2, "name": "Project 2", "created_at": "2024-01-02T00:00:00Z" }
  ]
  ```
- **Ошибки:** `401 Unauthorized`

---

### `POST /projects`
- **Назначение:** Создание нового проекта.
- **Тело запроса:**
  ```json
  { "name": "My Project" }
  ```
- **Пример ответа:**
  ```json
  { "id": 3, "name": "My Project", "created_at": "2024-01-03T00:00:00Z" }
  ```
- **Ошибки:** `400 Bad Request`, `401 Unauthorized`

---

### `GET /projects/{id}`
- **Назначение:** Получение информации о проекте.
- **Пример ответа:**
  ```json
  { "id": 1, "name": "Project 1", "created_at": "2024-01-01T00:00:00Z", "sources": [ ... ] }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

### `PATCH /projects/{id}`
- **Назначение:** Обновление информации о проекте.
- **Тело запроса:**
  ```json
  { "name": "New Project Name" }
  ```
- **Пример ответа:**
  ```json
  { "id": 1, "name": "New Project Name", "created_at": "2024-01-01T00:00:00Z" }
  ```
- **Ошибки:** `400 Bad Request`, `404 Not Found`, `401 Unauthorized`

---

### `DELETE /projects/{id}`
- **Назначение:** Удаление проекта.
- **Пример ответа:**
  ```json
  { "detail": "Project deleted" }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

## 4. Источники (каналы)

### `POST /projects/{id}/sources`
- **Назначение:** Добавление источника (канала) в проект.
- **Тело запроса:**
  ```json
  { "type": "rss", "url": "https://site.com/rss" }
  ```
- **Пример ответа:**
  ```json
  { "id": 10, "type": "rss", "url": "https://site.com/rss" }
  ```
- **Ошибки:** `400 Bad Request`, `404 Not Found`, `401 Unauthorized`

---

### `DELETE /projects/{id}/sources/{source_id}`
- **Назначение:** Удаление источника из проекта.
- **Пример ответа:**
  ```json
  { "detail": "Source deleted" }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

## 5. Посты

### `GET /projects/{id}/posts`
- **Назначение:** Получение списка постов проекта с поддержкой пагинации и расширенных фильтров.
- **Query params:**  
  - `page` — номер страницы (по умолчанию 1)  
  - `limit` — количество элементов на странице  
  - `statuses` — список статусов через запятую (`new,used,deleted`)  
  - `search` — строка для полнотекстового поиска по тексту поста и заголовку источника  
  - `include_keywords` — ключевые слова, из которых хотя бы одно должно встретиться в тексте поста  
  - `exclude_keywords` — ключевые слова, наличие которых исключает пост из выборки  
  - `has_media` — `true` / `false` для фильтрации по наличию вложений  
  - `date_from`, `date_to` — диапазон даты публикации (ISO 8601)  
  - `source_ids` — список идентификаторов источников через запятую  
- **Пример ответа:**
  ```json
  {
    "items": [
      { "id": 100, "title": "Post 1", "created_at": "2024-01-05T00:00:00Z" },
      { "id": 101, "title": "Post 2", "created_at": "2024-01-06T00:00:00Z" }
    ],
    "total": 2,
    "keyword_summary": {
      "презентации": 1
    }
  }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

### `GET /projects/{id}/posts/{post_id}`
- **Назначение:** Получение одного поста.
- **Пример ответа:**
  ```json
  { "id": 100, "title": "Post 1", "content": "...", "created_at": "2024-01-05T00:00:00Z" }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

### `DELETE /projects/{id}/posts/{post_id}`
- **Назначение:** Удаление поста.
- **Пример ответа:**
  ```json
  { "detail": "Post deleted" }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

### `POST /projects/{id}/posts/delete`
- **Назначение:** Массовое удаление постов.
- **Тело запроса:**
  ```json
  { "ids": [100, 101, 102] }
  ```
- **Пример ответа:**
  ```json
  { "deleted": [100, 101, 102] }
  ```
- **Ошибки:** `400 Bad Request`, `404 Not Found`, `401 Unauthorized`

---

## 6. Сюжеты

### `POST /projects/{id}/stories`
- **Назначение:** Создание нового сюжета.
- **Тело запроса:**
  ```json
  { "title": "Story Title", "post_ids": [100, 101] }
  ```
- **Пример ответа:**
  ```json
  { "id": 200, "title": "Story Title", "post_ids": [100, 101], "status": "draft" }
  ```
- **Ошибки:** `400 Bad Request`, `404 Not Found`, `401 Unauthorized`

---

### `GET /projects/{id}/stories`
- **Назначение:** Получение списка сюжетов проекта.
- **Пример ответа:**
  ```json
  [
    { "id": 200, "title": "Story 1", "status": "draft" },
    { "id": 201, "title": "Story 2", "status": "published" }
  ]
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

### `GET /stories/{story_id}`
- **Назначение:** Получение информации о сюжете.
- **Пример ответа:**
  ```json
  { "id": 200, "title": "Story 1", "post_ids": [100, 101], "status": "draft" }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

### `PATCH /stories/{story_id}`
- **Назначение:** Обновление сюжета.
- **Тело запроса:**
  ```json
  { "title": "New Title" }
  ```
- **Пример ответа:**
  ```json
  { "id": 200, "title": "New Title", "post_ids": [100, 101], "status": "draft" }
  ```
- **Ошибки:** `400 Bad Request`, `404 Not Found`, `401 Unauthorized`

---

### `DELETE /stories/{story_id}`
- **Назначение:** Удаление сюжета.
- **Пример ответа:**
  ```json
  { "detail": "Story deleted" }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

## 7. Рерайтинг

### `POST /stories/{story_id}/rewrite`
- **Назначение:** Отправить сюжет на рерайтинг.
- **Пример ответа:**
  ```json
  { "task_id": "abc123", "status": "pending" }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

### `GET /stories/{story_id}/rewrite/status`
- **Назначение:** Получить статус задачи рерайтинга.
- **Пример ответа:**
  ```json
  { "status": "completed", "result": { ... } }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

## 8. Публикация

### `POST /stories/{story_id}/publish`
- **Назначение:** Публикация сюжета.
- **Тело запроса:**
  ```json
  { "target": "@channel_name" }
  ```
- **Пример ответа:**
  ```json
  {
    "publication_id": 300,
    "status": "published",
    "message_ids": [2456]
  }
  ```
- **Ошибки:** `400 Bad Request`, `404 Not Found`, `401 Unauthorized`

---

### `POST /stories/{story_id}/schedule`
- **Назначение:** Отложенная публикация сюжета.
- **Тело запроса:**
  ```json
  { "publish_at": "2024-01-10T10:00:00Z" }
  ```
- **Пример ответа:**
  ```json
  { "publication_id": 301, "status": "scheduled", "publish_at": "2024-01-10T10:00:00Z" }
  ```
- **Ошибки:** `400 Bad Request`, `404 Not Found`, `401 Unauthorized`

---

### `GET /publications`
- **Назначение:** Получение списка публикаций пользователя.
- **Пример ответа:**
  ```json
  [
    { "id": 300, "story_id": 200, "status": "published", "published_at": "2024-01-07T12:00:00Z" }
  ]
  ```
- **Ошибки:** `401 Unauthorized`

---

## 9. Генерация изображений

### `POST /stories/{story_id}/image`
- **Назначение:** Генерация изображения для сюжета.
- **Тело запроса:**
  ```json
  { "prompt": "A sunny day in Paris" }
  ```
- **Пример ответа:**
  ```json
  { "image_id": 500, "url": "https://..." }
  ```
- **Ошибки:** `400 Bad Request`, `404 Not Found`, `401 Unauthorized`

---

### `DELETE /images/{image_id}`
- **Назначение:** Удаление изображения.
- **Пример ответа:**
  ```json
  { "detail": "Image deleted" }
  ```
- **Ошибки:** `404 Not Found`, `401 Unauthorized`

---

## 10. Ошибки и статусы

- **Формат ошибки:**
  ```json
  {
    "detail": "Error message"
  }
  ```
- **Примеры:**
  - `401 Unauthorized`:
    ```json
    { "detail": "Not authenticated" }
    ```
  - `404 Not Found`:
    ```json
    { "detail": "Not found" }
    ```
  - `400 Bad Request`:
    ```json
    { "detail": "Invalid input" }
    ```
