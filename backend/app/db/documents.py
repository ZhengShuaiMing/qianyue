"""用户材料（基础简历、补充资料）的读写。每次保存是一个新版本，旧版本保留。"""

import hashlib
from collections.abc import Callable

from sqlmodel import Session, col, select

from app.db.engine import serialized
from app.db.models import Document, DocumentKind


def content_hash_of(content_md: str) -> str:
    return hashlib.sha256(content_md.strip().encode("utf-8")).hexdigest()


def get_current(session: Session, kind: DocumentKind) -> Document | None:
    statement = select(Document).where(Document.kind == kind, col(Document.is_current).is_(True))
    return session.exec(statement).first()


@serialized
def save_document(
    session: Session, kind: DocumentKind, content_md: str, title: str = ""
) -> tuple[Document, bool]:
    """保存一个新版本，返回 (材料, 是否新增)。内容和当前版本相同时不新增。

    排队执行：材料页和助手同时保存时，不会出现两个「当前版本」。
    """
    return _save(session, kind, content_md, title)


@serialized
def edit_document(
    session: Session, kind: DocumentKind, change: Callable[[str], str]
) -> tuple[Document, bool]:
    """在当前版本上改出一个新版本（例如往 STAR 经历里加一段）。读和写在同一次排队里，
    两边同时改不会丢掉其中一边的内容。"""
    current = get_current(session, kind)
    return _save(session, kind, change(current.content_md if current else ""))


def _save(session: Session, kind: DocumentKind, content_md: str, title: str = ""):
    content_hash = content_hash_of(content_md)
    current = get_current(session, kind)
    if current and current.content_hash == content_hash:
        return current, False

    if current:
        current.is_current = False
        session.add(current)
    document = Document(kind=kind, title=title, content_md=content_md, content_hash=content_hash)
    session.add(document)
    session.commit()
    session.refresh(document)
    return document, True


def list_versions(session: Session, kind: DocumentKind) -> list[Document]:
    statement = select(Document).where(Document.kind == kind).order_by(col(Document.id).desc())
    return list(session.exec(statement).all())


def get_version(session: Session, kind: DocumentKind, document_id: int) -> Document | None:
    document = session.get(Document, document_id)
    return document if document and document.kind == kind else None
