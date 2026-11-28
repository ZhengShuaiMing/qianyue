"""从文字里取出「可核对的事实」：数字和英文技术词。

定制简历和沟通草稿都用它检查：新写的内容里，有没有材料之外的数字和技术词。
"""

import re

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+#.\-]*")
_LIST_NUMBER = re.compile(r"^\s*\d+[.、)）]\s*", re.MULTILINE)


def numbers(text: str) -> set[str]:
    """文字里的数字。行首的列表序号（1. 2、）不算。"""
    return set(_NUMBER.findall(_LIST_NUMBER.sub("", text)))


def terms(text: str) -> set[str]:
    """英文技术词，统一成小写。单个字母不算。"""
    return {term.rstrip(".-").lower() for term in _TERM.findall(text) if len(term.rstrip(".-")) > 1}


def unsupported(new_text: str, allowed_text: str) -> list[str]:
    """new_text 里出现、而 allowed_text 里没有的数字和技术词。

    材料里写的是 Node.js，新内容里单写 Node 或 JS 也算有出处。
    """
    known = terms(allowed_text)
    known |= {part for term in known for part in re.split(r"[.\-]", term) if len(part) > 1}
    return sorted(numbers(new_text) - numbers(allowed_text)) + sorted(terms(new_text) - known)
