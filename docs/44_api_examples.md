

# API Examples

## 1. Обзор

Этот документ содержит примеры использования REST API сервиса. Он предназначен для разработчиков и тестировщиков, чтобы быстро ознакомиться с форматами запросов и ответов, а также типичными ошибками. В каждом разделе приведены реальные JSON-запросы и ответы, а также структура ошибок.

---

## 2. Аутентификация

### 2.1 Логин
**Request**
```http
POST /api/auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "secret123"
}
```
**Response**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```
**Error**
```json
{
  "error": "Invalid credentials",
  "code": 401
}
```

### 2.2 Получение профиля
**Request**
```http
GET /api/users/me
Authorization: Bearer <access_token>
```
**Response**
```json
{
  "id": 17,
  "email": "user@example.com",
  "name": "Ivan Petrov",
  "role": "admin"
}
```
---

## 3. Проекты

### 3.1 Создание проекта
**Request**
```http
POST /api/projects
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "name": "My News Project",
  "description": "Сбор новостей по ИИ"
}
```
**Response**
```json
{
  "id": 22,
  "name": "My News Project",
  "description": "Сбор новостей по ИИ",
  "created_at": "2024-06-01T10:23:45Z"
}
```

### 3.2 Получение списка проектов
**Request**
```http
GET /api/projects
Authorization: Bearer <access_token>
```
**Response**
```json
[
  {
    "id": 22,
    "name": "My News Project",
    "description": "Сбор новостей по ИИ"
  },
  {
    "id": 23,
    "name": "Tech Digest",
    "description": ""
  }
]
```

### 3.3 Удаление проекта
**Request**
```http
DELETE /api/projects/22
Authorization: Bearer <access_token>
```
**Response**
```json
{
  "success": true
}
```
**Error**
```json
{
  "error": "Project not found",
  "code": 404
}
```

---

## 4. Источники (Sources)

### 4.1 Добавление источника
**Request**
```http
POST /api/projects/22/sources
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "type": "rss",
  "url": "https://tass.ru/rss/v2.xml"
}
```
**Response**
```json
{
  "id": 101,
  "type": "rss",
  "url": "https://tass.ru/rss/v2.xml",
  "added_at": "2024-06-01T10:35:00Z"
}
```

### 4.2 Удаление источника
**Request**
```http
DELETE /api/projects/22/sources/101
Authorization: Bearer <access_token>
```
**Response**
```json
{
  "success": true
}
```

---

## 5. Посты

### 5.1 Получение списка постов с фильтрами и пагинацией
**Request**
```http
GET /api/projects/22/posts?limit=2&offset=0&source_id=101
Authorization: Bearer <access_token>
```
**Response**
```json
{
  "count": 12,
  "results": [
    {
      "id": 501,
      "title": "ИИ обыграл чемпиона мира",
      "published_at": "2024-06-01T12:10:00Z",
      "source_id": 101
    },
    {
      "id": 502,
      "title": "Новая статья о машинном обучении",
      "published_at": "2024-06-01T11:50:00Z",
      "source_id": 101
    }
  ]
}
```

### 5.2 Пример полного поста
**Request**
```http
GET /api/posts/501
Authorization: Bearer <access_token>
```
**Response**
```json
{
  "id": 501,
  "title": "ИИ обыграл чемпиона мира",
  "content": "Вчера искусственный интеллект впервые победил...",
  "published_at": "2024-06-01T12:10:00Z",
  "source": {
    "id": 101,
    "type": "rss",
    "url": "https://tass.ru/rss/v2.xml"
  },
  "url": "https://tass.ru/news/ai-win",
  "tags": ["ИИ", "шахматы"]
}
```

---

## 6. Сюжеты (Stories)

### 6.1 Создание сюжета из постов
**Request**
```http
POST /api/projects/22/stories
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "title": "ИИ в шахматах",
  "post_ids": [501, 502]
}
```
**Response**
```json
{
  "id": 301,
  "title": "ИИ в шахматах",
  "posts": [501, 502],
  "created_at": "2024-06-01T13:00:00Z"
}
```

### 6.2 Получение сюжета
**Request**
```http
GET /api/stories/301
Authorization: Bearer <access_token>
```
**Response**
```json
{
  "id": 301,
  "title": "ИИ в шахматах",
  "posts": [
    {
      "id": 501,
      "title": "ИИ обыграл чемпиона мира"
    },
    {
      "id": 502,
      "title": "Новая статья о машинном обучении"
    }
  ]
}
```

### 6.3 Обновление сюжета
**Request**
```http
PATCH /api/stories/301
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "title": "ИИ и шахматы: новые горизонты"
}
```
**Response**
```json
{
  "id": 301,
  "title": "ИИ и шахматы: новые горизонты"
}
```

---

## 7. Рерайт (Rewrite)

### Пример запроса к OpenAI и результата
**Request**
```http
POST /api/rewrite
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "text": "ИИ обыграл чемпиона мира по шахматам.",
  "style": "formal"
}
```
**Response**
```json
{
  "rewritten_text": "Искусственный интеллект одержал победу над чемпионом мира по шахматам."
}
```
**Error**
```json
{
  "error": "Rewrite service unavailable",
  "code": 503
}
```

---

## 8. Публикация

### 8.1 Публикация поста
**Request**
```http
POST /api/posts/501/publish
Authorization: Bearer <access_token>
```
**Response**
```json
{
  "status": "published",
  "published_at": "2024-06-01T14:00:00Z"
}
```

### 8.2 Отложенная публикация
**Request**
```http
POST /api/posts/501/publish
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "publish_at": "2024-06-02T09:00:00Z"
}
```
**Response**
```json
{
  "status": "scheduled",
  "publish_at": "2024-06-02T09:00:00Z"
}
```

---

## 9. Генерация изображений

### 9.1 Генерация картинки
**Request**
```http
POST /api/images
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "prompt": "Робот играет в шахматы",
  "style": "digital art"
}
```
**Response**
```json
{
  "id": 900,
  "url": "https://cdn.paperbird.ai/images/900.png",
  "created_at": "2024-06-01T15:00:00Z"
}
```

### 9.2 Удаление картинки
**Request**
```http
DELETE /api/images/900
Authorization: Bearer <access_token>
```
**Response**
```json
{
  "success": true
}
```

---

## 10. Ошибки и ответы

### Стандартная структура ошибок
```json
{
  "error": "Message describing the error",
  "code": 400
}
```

### Пример сообщения об ошибке
```json
{
  "error": "Validation failed: field 'title' is required",
  "code": 422
}
```