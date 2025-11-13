# Руководство по JSON-пресетам веб-парсера Paperbird

Документ обновлён на основе фактического кода (`src/projects/services/web_preset_registry.py` и `src/projects/schemas/web_preset.schema.json`). Ниже описано:
- как устроен импорт (`WebPresetRegistry.import_payload`);
- как выполняется валидация (`WebPresetValidator` + JSON Schema);
- какой JSON требуется, чтобы пресет прошёл проверки.

Эти инструкции можно пересылать в ChatGPT: модель должна вернуть JSON, удовлетворяющий всем описанным требованиям, без выдуманных полей.

## 1. Как устроен парсер и импорт

1. Фронтенд/формы передают JSON-строку в `WebPresetRegistry.import_payload`.
2. Метод `_parse` пытается распарсить строку через `json.loads`. Любая синтаксическая ошибка сразу превращается в `PresetValidationError` с сообщением «Некорректный JSON: …».
3. Полученный словарь передают в `WebPresetValidator.validate`. Валидатор лениво загружает схему `web_preset.schema.json` и использует `jsonschema.Draft202012Validator`.
4. Если JSON не соответствует схеме, `jsonschema` возвращает детализированное сообщение — его мы также оборачиваем в `PresetValidationError`.
5. При успехе вычисляется SHA‑256 от всего payload (сортировка ключей включена) и фиксируются `name`, `version`, `schema_version`.
6. `WebPresetRegistry` ищет запись `WebPreset` по `(name, version)`, обновляет `config`, `checksum`, `schema_version`, `title`, `description` и, если нужно, статус (активный по умолчанию).

Следствие: **единственный способ пройти импорт — строго соблюдать JSON Schema**. Лишние ключи запрещены, потому что почти у всех объектов стоит `"additionalProperties": false`.

- После успешного импорта активного пресета все связанные `Source(web)` автоматически получают свежий `web_preset_snapshot`, поэтому воркер начинает использовать новые заголовки и селекторы без ручных правок. Если нужно вернуться к прошлой версии, создайте новый пресет с другой версией и переключите источник.

## 2. Общие правила схемы

- Draft 2020-12, путь к файлу: `src/projects/schemas/web_preset.schema.json`.
- Верхнеуровневой тип — объект без лишних ключей.
- Обязательные поля: `name`, `version`, `match`, `fetch`, `list_page`, `article_page`.
- `schema_version` — необязательное целое ≥1 (по умолчанию 1).
- Все вложенные объекты (`match`, `fetch`, `list_page`, `article_page`, `render`, `normalize`, `tests` и т.д.) принимают **только** перечисленные свойства.

### 2.1 `name`
- Строка, регэксп `^[a-z0-9_\-]{3,80}$`.
- Используйте латиницу в нижнем регистре, цифры, подчёркивания и дефисы.

### 2.2 `version`
- Строка, семантическая версия: `MAJOR.MINOR.PATCH`, допускаются суффиксы `-alpha`, `+meta` и т.п. (`^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9\.]+)?$`).

### 2.3 `description`
- Необязательная строка ≤500 символов.

## 3. Блок `match`

```json
"match": {
  "domains": ["example.com", "www.example.com"],
  "include": ["https://example.com/news"],
  "exclude": ["https://example.com/archive"]
}
```

- Обязателен массив `domains` (минимум 1). Каждая строка должна соответствовать `^[A-Za-z0-9_.\-]+$`.
- `include`/`exclude` — массивы строк (паттерны URL), опциональны.

## 4. Блок `fetch`

```json
"fetch": {
  "timeout_sec": 10,
  "rate_limit_rps": 0.5,
  "headers": {
    "User-Agent": "PaperbirdWebCollector/1.0 (+https://paperbird.ai)"
  },
  "robots_policy": "respect"
}
```

- Обязателен `timeout_sec` (число от 1 до 120).
- `rate_limit_rps` > 0, если нужен лимит запросов.
- `headers` — объект ключ/значение строка.
- `robots_policy` — `"respect"` или `"ignore"`.

## 5. Блок `render` (по желанию)

```json
"render": {
  "enabled": true,
  "wait_for": ".app-loaded",
  "timeout_sec": 20
}
```

- Если не нужен headless-браузер, пропустите блок или оставьте `enabled: false`.

## 6. Блок `list_page`

```json
"list_page": {
  "seeds": ["https://example.com/news"],
  "url_prefix": "https://example.com",
  "selectors": {
    "items": "article.card",
    "url": "a@href",
    "title": "a@text",
    "published_at": "time@datetime"
  },
  "pagination": {
    "type": "selector",
    "selector": "a.next",
    "max_pages": 5
  }
}
```

- Обязателен объект `selectors` с обязательным полем `items`. Допустимые ключи: `items`, `url`, `title`, `published_at`.
- **Важно:** ключи должны называться ровно `items`, `url`, `title`, `published_at`. Схема запрещает любые синонимы (`item`, `link`, `headline` и т.д.), поэтому при генерации через GPT явно просите использовать эти имена, иначе пресет не пройдёт импорт.
- `seeds` — массив абсолютных URL, минимум один элемент.
- `url_prefix` — строка с `format: uri` (используется, если сайт отдаёт относительные ссылки).
- `pagination` обязана иметь `type` (`none`, `selector` или `token`). `selector` — строка, `max_pages` (1‑20) ограничивает глубину.
- Дополнительных ключей вроде `filters` в схеме нет, поэтому их писать нельзя.

## 7. Блок `article_page`

```json
"article_page": {
  "selectors": {
    "title": "h1.headline",
    "published_at": "time@datetime",
    "content": "div.article-body",
    "images": "div.article-body img@src",
    "canonical_url": "link[rel='canonical']@href",
    "source_url": "meta[property='og:url']@content",
    "summary": "meta[name='description']@content",
    "category": ".breadcrumbs li:last-child@text",
    "author": ".article-author@text",
    "source_name": ".source@text"
  },
  "cleanup": {
    "remove": ["div.advert", "aside.subscribe"],
    "unwrap": ["figure"]
  },
  "normalize": {
    "html_to_md": true,
    "collapse_whitespace": true,
    "make_absolute_urls": true,
    "strip_tracking_params": true
  },
  "media": {
    "images": {
      "prefix": "https://cdn.example.com",
      "min_width": 640
    }
  }
}
```

- `selectors` — обязательный объект (но внутри можно оставлять только нужные ключи из списка: `title`, `published_at`, `content`, `images`, `canonical_url`, `source_url`, `summary`, `category`, `author`, `source_name`).
- **Без кастомных имён:** если нужен второй заголовок, аннотация и т.п., используйте существующие ключи (`summary`, `content` и т.д.). Поля вроде `subtitle`, `lead`, `teaser` не описаны в схеме и вызовут ошибку валидации.
- `cleanup.remove` / `cleanup.unwrap` — массивы строк-селекторов.
- `normalize` допускает только четыре булевых флага: `html_to_md`, `collapse_whitespace`, `make_absolute_urls`, `strip_tracking_params`.
- `media.images.prefix` должен быть `format: uri`; `min_width` ≥1.

## 8. Дополнительные блоки

- `transformers.normalize`: объект без ограничений по ключам (используется под дополнительные нормализаторы).
- `normalize` на верхнем уровне — второй уровень нормализации (булевые флаги `collapse_whitespace`, `strip_tracking_params`, `make_absolute_urls`).
- `tests`: массив объектов `{ "url": "<absolute>", "expect": { ... } }`, где `expect` поддерживает:
  - `title_contains` — строка;
  - `content_min_len` — целое ≥1;
  - `images_count_min` — целое ≥0.

## 9. Пример валидного пресета

```json
{
  "name": "example_news",
  "version": "1.0.0",
  "description": "Новости example.com",
  "schema_version": 1,
  "match": {
    "domains": ["example.com", "www.example.com"]
  },
  "fetch": {
    "timeout_sec": 15,
    "rate_limit_rps": 0.5,
    "headers": {
      "User-Agent": "PaperbirdWebCollector/1.0 (+https://paperbird.ai)"
    },
    "robots_policy": "respect"
  },
  "render": {
    "enabled": false
  },
  "list_page": {
    "seeds": ["https://www.example.com/news"],
    "selectors": {
      "items": "article.card",
      "url": "a@href",
      "title": "a@text",
      "published_at": "time@datetime"
    },
    "pagination": {
      "type": "selector",
      "selector": "a.next",
      "max_pages": 3
    }
  },
  "article_page": {
    "selectors": {
      "title": "h1",
      "content": "div.article-body",
      "images": "div.article-body img@src",
      "published_at": "time@datetime"
    },
    "cleanup": {
      "remove": ["div.ad", "aside.banner"]
    },
    "normalize": {
      "html_to_md": true,
      "make_absolute_urls": true
    }
  },
  "tests": [
    {
      "url": "https://www.example.com/news/123",
      "expect": {
        "title_contains": "Example",
        "content_min_len": 1200,
        "images_count_min": 1
      }
    }
  ]
}
```

Этот JSON удовлетворяет всем ограничениям схемы и будет принят валидатором.

## 10. Как просить ChatGPT

1. Скопируйте этот документ (можно сокращённый вариант с секциями 2‑8 и примером).
2. Добавьте конкретные требования: домены, разделы, какие поля важны (дата, автор, изображения).
3. Попросите не добавлять неописанные свойства и соблюдать `additionalProperties: false`.
4. После ответа проверьте JSON локально:
   ```bash
   python manage.py shell -c "from projects.services.web_preset_registry import WebPresetValidator; import json, sys; WebPresetValidator().validate(json.load(sys.stdin))"
   ```
   или импортируйте через UI — валидатор выдаст точную ошибку, если что-то нарушено.

Следуя этим правилам, пресет гарантированно пройдёт проверку и активируется без ручных правок.

## 11. Дополнительные требования для корректного парсинга

Эти пункты особенно важны для генерации подсказок ChatGPT:

- **Всегда проверяйте, что `article_page.selectors.content` охватывает весь текст.** На сайтах, где каждый абзац лежит в отдельном блоке (например, `.article__text`), необходимо использовать множественный селектор с `*` (`"div.article__text*"`), либо выбрать оборачивающий контейнер (например, `"div.article__body"`). Отсутствие `*` приводит к тому, что коллектор сохраняет только первый абзац.
- **Если нет единого контейнера, дайте множественный селектор и тест, подтверждающий длину текста.** В `tests[].expect.content_min_len` задавайте реалистичное значение (≥500 символов для полноценных новостей), чтобы автотесты ловили случаи, когда собрали только заголовок или лид.
- **Явно перечисляйте все блоки, которые нужно удалить перед нормализацией.** Хедеры «Читать в Telegram», блоки рекомендованных материалов и рекламные вставки нужно добавлять в `cleanup.remove`, иначе в feed попадут мусорные фрагменты.
- **Убедитесь, что изображения собираются списком.** Если на странице несколько `<img>`, используйте `img@src*` и настройку `media.images.strip_tracking_params`, чтобы избежать дубликатов.
- **Описание тест-кейсов должно ссылаться на реальные URL.** Чем ближе тестовая статья к живому сценарию (та же рубрика/шаблон), тем быстрее мы поймаем изменения верстки.

Формулируя задание для ChatGPT, перечислите эти требования отдельным пунктом. Модель должна явно проверить страницу: «если текст размечен поквартально — выбирай селектор с `*`, иначе возьми контейнер». Это минимизирует случаи, когда в ленту попадает только заголовок без основного тела статьи.
