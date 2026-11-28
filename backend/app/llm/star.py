"""STAR 经历：把用户讲的一段经历排成固定的格式，并把它放进经历文档。

经历文档是一份 Markdown，用二级标题分隔每段经历（定制简历和评估从里面取证据）。
这里只做排版和拼接。内容是不是用户说过的，由调用它的工具核对。
"""

import re

PARTS = (("situation", "情境"), ("task", "任务"), ("action", "行动"), ("result", "结果"))


def render_entry(title: str, parts: dict[str, str]) -> str:
    lines = [f"## {title.strip()}", ""]
    lines += [f"- **{name}:** {' '.join(parts[key].split())}" for key, name in PARTS]
    return "\n".join(lines)


def find_entry(document_md: str, title: str) -> tuple[int, int] | None:
    """同名经历在文档里的位置（起、止），没有就是 None。"""
    heading = re.compile(rf"^##[ \t]+{re.escape(title.strip())}[ \t]*$", re.MULTILINE)
    found = heading.search(document_md)
    if found is None:
        return None
    following = re.compile(r"^#{1,2}[ \t]+\S", re.MULTILINE).search(document_md, found.end())
    return found.start(), following.start() if following else len(document_md)


def put_entry(document_md: str, title: str, entry: str) -> str:
    """把一段经历放进文档：同名的整段替换，没有就加在最后。"""
    place = find_entry(document_md, title)
    if place is None:
        return (document_md.rstrip() + "\n\n" + entry).lstrip() + "\n"
    start, end = place
    rest = document_md[end:].lstrip("\n")
    return document_md[:start] + entry + ("\n\n" + rest if rest else "\n")
