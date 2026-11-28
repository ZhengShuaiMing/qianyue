"""已通过校验的脱敏引文，仅在本地定位原文。"""

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from itertools import accumulate

from app.llm.parsing import compact
from app.llm.privacy import PLACEHOLDERS

# 普通引文继续忽略空白和标点，脱敏占位符保持为一个字符。
_PLACEHOLDER_MARKS = dict(zip(PLACEHOLDERS, "\ue000\ue001\ue002", strict=True))
_IGNORED = re.compile(r"[^\w\ue000-\ue002]|_")


def plain_quote(text: str) -> str:
    for token, mark in _PLACEHOLDER_MARKS.items():
        text = text.replace(token, mark * len(token[1:-1]))
    return _IGNORED.sub("", unicodedata.normalize("NFKC", text)).lower()


@dataclass(frozen=True)
class QuoteSource:
    name: str
    original: str
    visible: str


def quote_sources(materials: dict[str, str], mask: Callable[[str], str]) -> list[QuoteSource]:
    return [QuoteSource(name, text, mask(text)) for name, text in materials.items()]


def prefixed_source(name: str, text: str, prefix: str, mask: Callable[[str], str]) -> QuoteSource:
    """同一行的说话人标签也参与脱敏，但不参与原话核对。"""
    return QuoteSource(name, text, mask(prefix + text).removeprefix(mask(prefix)))


def has_placeholder(text: str) -> bool:
    return any(token in text for token in PLACEHOLDERS)


def local_quote(
    quote: str,
    sources: list[QuoteSource],
    *,
    normalize: Callable[[str], str] = compact,
    pieces: list[str] | None = None,
) -> str:
    """普通引文原样展示。脱敏引文仅在原行唯一时展示该行。"""
    if not has_placeholder(quote) or all(s.original == s.visible for s in sources):
        return quote

    pieces = [normalize(quote)] if pieces is None else pieces
    references = []
    original_excerpt = None
    for source in sources:
        lines = source.visible.splitlines()
        original_lines = source.original.splitlines()
        if len(lines) != len(original_lines):
            references.append(source.name)
            continue
        normalized = [normalize(line) for line in lines]
        offsets = list(accumulate(map(len, normalized), initial=0))
        text = "".join(normalized)
        spans = [(text.find(piece), text.rfind(piece) + len(piece) - 1) for piece in pieces]
        if not spans or any(start < 0 for start, _ in spans):
            references.append(source.name)
            continue
        first = bisect_right(offsets, min(start for start, _ in spans))
        last = bisect_right(offsets, max(end for _, end in spans))
        line_range = str(first) if first == last else f"{first}–{last}"
        references.append(f"{source.name}第 {line_range} 行")
        if spans == sorted(spans) and all(
            end - start + 1 == len(piece) for (start, end), piece in zip(spans, pieces, strict=True)
        ):
            original_excerpt = "\n".join(original_lines[first - 1 : last])
    if len(references) == 1 and original_excerpt is not None:
        return original_excerpt
    return f"{quote}（原文位置：{'、'.join(references)}）"
