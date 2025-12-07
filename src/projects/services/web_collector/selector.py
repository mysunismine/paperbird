"""Selector helpers used by the web collector."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - optional dependency guard
    from bs4 import BeautifulSoup, Tag  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    Tag = Any  # type: ignore


class SelectorEngine:
    """Minimal CSS selector helper with DSL parsing."""

    def __init__(self, parser: str = "html.parser") -> None:
        self.parser = parser

    def parse(self, html: str) -> BeautifulSoup:
        if BeautifulSoup is None:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "beautifulsoup4 не установлен. Выполните `pip install -r requirements.txt`."
            )
        return BeautifulSoup(html, self.parser)

    def select_items(self, soup: BeautifulSoup, selector: str) -> list[Tag]:
        return list(soup.select(selector))

    def extract(self, node: Tag | BeautifulSoup, expression: str) -> Any:
        spec = self._parse_expression(expression)
        nodes: Iterable[Tag]
        if spec.selector:
            nodes = node.select(spec.selector)
        else:
            nodes = [node]
        if spec.multiple:
            return [self._extract_value(n, spec.attribute) for n in nodes]
        try:
            target = next(iter(nodes))
        except StopIteration:
            if spec.optional:
                return None
            raise LookupError(f"Selector '{expression}' returned nothing") from None
        return self._extract_value(target, spec.attribute)

    def _extract_value(self, node: Tag, attribute: str | None) -> str:
        if attribute is None:
            return node.decode_contents().strip()
        if attribute == "text":
            return node.get_text(strip=True)
        return (node.get(attribute) or "").strip()

    @dataclass(slots=True)
    class Expression:
        selector: str | None
        attribute: str | None
        multiple: bool
        optional: bool

    def _parse_expression(self, expression: str) -> Expression:
        optional = expression.endswith("?")
        multiple = expression.endswith("*")
        expr = expression
        if optional:
            expr = expr[:-1]
        if multiple:
            expr = expr[:-1]
        selector = expr
        attribute = None
        if "@" in expr:
            selector, attribute = expr.split("@", 1)
        selector = selector or None
        attribute = attribute or None
        return SelectorEngine.Expression(
            selector=selector.strip() if selector else None,
            attribute=attribute.strip() if attribute else None,
            multiple=multiple,
            optional=optional,
        )
