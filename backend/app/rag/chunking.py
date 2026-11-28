"""把 Markdown 材料切成适合检索的片段。

按标题切：一段工作经历、一个项目、一段 STAR 经历各自成片，片段带着它所在的标题路径，
这样检索「RAG 项目经验」时拿回来的是一段完整的经历，而不是半句话。
"""

from dataclasses import dataclass

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

CHUNK_SIZE = 400  # 一个片段最多多少字。太长会稀释语义，太短会丢上下文

_by_heading = MarkdownHeaderTextSplitter([("#", "h1"), ("##", "h2"), ("###", "h3")])
_by_length = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE, chunk_overlap=0, separators=["\n\n", "\n", "。", "；", ""]
)


@dataclass(frozen=True)
class Chunk:
    heading: str  # 标题路径，例如「工作经历 > 某公司」
    text: str


def split_markdown(content_md: str) -> list[Chunk]:
    chunks = []
    for section in _by_heading.split_text(content_md):
        heading = " > ".join(section.metadata[level] for level in sorted(section.metadata))
        for text in _by_length.split_text(section.page_content):
            if text.strip():
                chunks.append(Chunk(heading=heading, text=text.strip()))
    return chunks
