"""Сервисы для фильтрации постов по расширенным условиям."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import Q, QuerySet

from projects.models import Post


def _normalize_keyword_set(values: Iterable[str]) -> set[str]:
    """Нормализует набор ключевых слов: удаляет дубликаты и лишние пробелы."""

    normalized: dict[str, str] = {}
    for value in values:
        if not value:
            continue
        cleaned = str(value).strip()
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if lowered in normalized:
            continue
        normalized[lowered] = cleaned
    return set(normalized.values())


def _normalize_search_terms(value: str | None) -> list[str]:
    """Разбивает поисковую строку на уникальные термины."""

    if not value:
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for raw in value.replace(",", " ").split():
        cleaned = raw.strip()
        if not cleaned:
            continue
        lowered = cleaned.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(cleaned)
    return terms


@dataclass(slots=True)
class PostFilterOptions:
    """Параметры фильтрации постов."""

    statuses: set[str] = field(default_factory=set)
    search: str = ""
    include_keywords: set[str] = field(default_factory=set)
    exclude_keywords: set[str] = field(default_factory=set)
    date_from: datetime | None = None
    date_to: datetime | None = None
    has_media: bool | None = None
    source_ids: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.statuses = {status for status in self.statuses if status}
        self.include_keywords = _normalize_keyword_set(self.include_keywords)
        self.exclude_keywords = _normalize_keyword_set(self.exclude_keywords)
        if self.search:
            self.search = self.search.strip()
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Дата начала фильтрации позже даты окончания")
        self.source_ids = {source_id for source_id in self.source_ids if source_id}

    @property
    def search_terms(self) -> list[str]:
        """Возвращает список термов для полнотекстового поиска."""

        return _normalize_search_terms(self.search)

    @property
    def highlight_keywords(self) -> set[str]:
        """Возвращает совокупность ключевых слов для подсветки в UI."""

        keywords = set(self.include_keywords)
        keywords.update(self.search_terms)
        return keywords


def apply_post_filters(queryset: QuerySet[Post], options: PostFilterOptions) -> QuerySet[Post]:
    """Применяет набор фильтров к queryset постов."""

    filtered = queryset

    if options.statuses:
        valid_statuses = set(Post.Status.values)
        unknown_statuses = options.statuses - valid_statuses
        if unknown_statuses:
            raise ValueError(
                f"Неизвестные статусы постов: {sorted(unknown_statuses)}"
            )
        filtered = filtered.filter(status__in=options.statuses)

    if options.source_ids:
        filtered = filtered.filter(source_id__in=options.source_ids)

    if options.date_from:
        filtered = filtered.filter(posted_at__gte=options.date_from)

    if options.date_to:
        filtered = filtered.filter(posted_at__lte=options.date_to)

    if options.has_media is True:
        filtered = filtered.filter(has_media=True)
    elif options.has_media is False:
        filtered = filtered.filter(has_media=False)

    if options.search_terms:
        for term in options.search_terms:
            term_q = (
                Q(message__icontains=term)
                | Q(source__title__icontains=term)
                | Q(source__username__icontains=term)
            )
            filtered = filtered.filter(term_q)

    if options.include_keywords:
        include_q = Q()
        for keyword in options.include_keywords:
            include_q |= Q(message__icontains=keyword)
        filtered = filtered.filter(include_q)

    if options.exclude_keywords:
        for keyword in options.exclude_keywords:
            filtered = filtered.exclude(message__icontains=keyword)

    return filtered.distinct()


def collect_keyword_hits(
    posts: Iterable[Post],
    keywords: Iterable[str],
) -> dict[int, list[str]]:
    """Определяет совпадения ключевых слов для списка постов."""

    normalized = {keyword.casefold(): keyword for keyword in keywords if keyword}
    results: dict[int, list[str]] = {}
    if not normalized:
        return results

    for post in posts:
        text = (post.message or "").casefold()
        matches: list[str] = []
        for lowered, original in normalized.items():
            if lowered in text:
                matches.append(original)
        if matches:
            results[post.id] = matches
    return results


def summarize_keyword_hits(posts: Iterable[Post], keywords: Iterable[str]) -> dict[str, int]:
    """Возвращает сводку количества совпадений по каждому ключевому слову."""

    counter: Counter[str] = Counter()
    normalized = {keyword.casefold(): keyword for keyword in keywords if keyword}
    if not normalized:
        return {}

    for post in posts:
        text = (post.message or "").casefold()
        for lowered, original in normalized.items():
            if lowered in text:
                counter[original] += 1
    return dict(counter)


__all__ = [
    "PostFilterOptions",
    "apply_post_filters",
    "collect_keyword_hits",
    "summarize_keyword_hits",
]
